# Handback d091673: the Tropics API serves storms, tracks by model run and place odds from the tropical bank

**Lane:** d091673 · **Repo:** energylake-api · **Branch:** `claude/tropics-api-storms-tracks-odds-1vlri2`, from main at `149e314`.
**Scope:** branch only. No PR, no merge, no deploy, no migration. No pantry change; no R2 read.
**Neon:** read-only through the connector, 2026-10-09 21:58–22:30Z. Every statement was a `SELECT` or an `EXPLAIN (ANALYZE, BUFFERS)` of one. Nothing was written.
**Pantry, read only:** `energylake-pantry` at `0bcb194`: migrations 291 and 293, `tropical/points.py`, `tropical/storms.py`, `tropical/parsers/*`, `tropical/nhc.py`, and the d091664 handback §1, §2, §4, §9 and §10.

---

## 0. In ten lines

1. **Four routes** under `/api/weather/tropics`: `storms`, `storm`, `tracks`, `odds` (§2). They are not merged: the page opens a storm with three parallel fetches, and stepping the player costs no fetch.
2. **Every read is index-backed on Neon.** `/storms` totals ≈ 0.34 ms; `/tracks` at n = 8 for Rachel is 3.3 ms over 115 buffers; the slowest read is `/storm`'s cycle census at 10.3 ms (§4).
3. **The obvious census was a seq scan of the whole points table.** So is the freshness view, at 280 ms. Both were replaced with index reads (§4).
4. **STOP-P fired.** `place_id` is NHC's printed name, and nothing in Neon holds a place's coordinates. The id is served as stored, the payload says so, and §6 names the pantry table that would fix it.
5. **STOP-Z did not fire.** The largest run-by-run body (Simon, n = 8) is 394 KB raw and **38 KB gzip**, 13× under the 500 KB line.
6. **The bank stores signed longitude** everywhere (−143.1 to 3.2). The API still guards the east-positive case its CHECK allows.
7. **The bank already corrected NHC's 3 h-late VALIDTIME.** Its CHECK holds `valid_ts = init_ts + tau` on every row. The API corrects nothing.
8. **An intermediate advisory shares its parent's `init_ts`.** So "newest by init_ts" ties; the tie is broken by the advisory's own position time, never by the string (§1.3).
9. **IVCN is not a track.** All 1,230 of its rows are at 0°, 0°. It is served as stored and marked `track: false`.
10. **Tests:** 87 new, red on main and green here. The rehearsal is 21 of 21 breaks red, tree restored byte for byte. The whole suite is **2,734 passed** (main: 2,647).

---

## 1. What was measured

### 1.1 The bank at the cut (2026-10-09 21:54:47.543942Z)

| table | rows | note |
|---|---:|---|
| `tropical_storms` | 3 | al092026 Isaias, ep182026 Rachel, ep202026 Simon; all `active` |
| `tropical_track_points` | 34,518 | 28 sources; `init_ts` 2026-09-25 → 10-09 18Z |
| `tropical_place_odds` | 2,674 | `nhc_pws` only; 9 issuances (3 per storm) |
| heartbeat rows | 31 | newest 21:54:46.943Z |

Per storm, model cycles (any source but best_track, tcvitals and nhc_official): Isaias 18, **Rachel 56**, Simon 18. Rows per cycle are 70–505, about 340–394 on average, across up to 25 sources. The newest cycle (18Z) held only 7 sources and 71–85 rows; §1.4 says why.

`tropical_storms.first_seen` is the bank's first sighting (10-07 18Z for all three), not genesis. Rachel's best track begins 09-25 00Z.

### 1.2 The four "not measured" items

| item | answer | how |
|---|---|---|
| **where a `place_id`'s name and coordinates live** | **Nowhere the API can read (STOP-P).** `place_id` is NHC's own 14-character printed name ("PANAMA CITY FL", "YUMA AZ", grid points such as "GFAM 290N 850W" and "25N 120W"). The `pws` ledger rows carry `meta = {}`. No table has a column for it: `information_schema` over every `place`, `gazet`, `pws`, `location` or `city` name finds only CPC's station `place` (USW…). | §3 |
| **whether lon is east-positive or signed** | **Signed, negative west, in every row:** min −143.1, max 3.2, 0 rows above 180. The parsers write negative west (`tropical/parsers/__init__.py`); the ATCF parser folds < −180 back into range; the CHECK still admits −180..360. | `SELECT … sum((lon>180)::int)` per source |
| **rows per storm per cycle (body size)** | 70–505 rows per cycle (§1.1). The run-by-run body is §5. | `GROUP BY storm_id, init_ts` |
| **which A-deck aids are early or late versions of one model** | **The bank holds no interpolated ("I" or "2") aid at all** (`NEON_ADECK_AIDS`), so no model appears both early and late. What it does hold twice is **one model through two feeds** (next table). | joins at equal `(storm, init_ts, tau)` |

**One model, two feeds:**

| pair | points paired | identical positions | max position gap | served as |
|---|---:|---:|---:|---|
| `atcf:OFCL` vs `nhc_official` | 58 | **58** | 0.00° | both; OFCL is a series, the GIS official rides with each cycle |
| `atcf:NVGM` vs `atcf:NGX` | 652 | 430 | 2.4° | both, each labelled NAVGEM with its aid id |
| `atcf:HFSA` vs `hafs_a` | 466 | 32 | 9.3° (mean 0.97°) | both, "HAFS-A (A-deck HFSA)" and "HAFS-A (NOAA tracker file)" |
| `atcf:HFSB` vs `hafs_b` | 447 | 31 | 5.8° | both, likewise |
| `best_track` vs `tcvitals` | 27 | 18 | 0.3° | both, tcvitals marked `operational` |

- HFSA and hafs_a are closest at the same init (mean 0.97° apart), against 1.5° at ±6 h. They are not the same numbers, so neither is dropped.
- `SOURCES[*].same_model_as` names the twin.

**Early and late, by when they land.** At 22:08Z the 18Z cycle held only the seven early aids: TVCN, HCCA, RVCN, IVCN, CLP5, OCD5 and TCLP. Every late model stopped at 12Z. `SOURCES[*].adeck` carries `early` or `late`, and `/tracks`' `absent[]` names the late ones missing from the newest cycle.

### 1.3 Advisories, intermediates and VALIDTIME

- **Spelling.** The bank normalises NHC's spellings to `NNN` or `NNNA` (`tropical/nhc.py` `norm_adv`). Neon holds no `008a` and no bare `8`.
- **An intermediate shares its parent's `init_ts`.** On all three storms, 012 and 012A (Isaias), 050 and 050A (Rachel), and 009 and 009A (Simon) share `init_ts`. They share every forecast point from tau 12 on: position, vmax, radii and stage are all equal.
  - The only difference is the storm's own position: tau 3 for the parent (issued at synoptic + 3 h), tau 6 for the intermediate (+ 6 h).
- **The rule served:** advisories are ordered by `init_ts`, then by the valid time of the advisory's own (first) point. This is never a string order.
- **VALIDTIME.** The bank corrected it. Its CHECK `ttp_valid_is_init_plus_tau` holds on every row, and the GIS parser dates points from the synoptic time and never reads VALIDTIME for tau > 0.
  - `valid_ts` is served as stored, and the bodies say so (`valid_ts_rule`).

### 1.4 Other things the bank states that the page must not misread

- **IVCN:** 1,230 rows, every one at lat 0 and lon 0 with no MSLP. It is an intensity consensus. It is served as stored, with `track: false` and a `track_note`.
- **tcvitals `stage` is the storm's name** ("ISAIAS", "RACHEL", "SIMON"), not a stage. It is served verbatim and never used for identity.
- **The official GIS has been banked since 10-09 06Z** (4 advisories per storm). An older cycle has `official: []` with `official_absence`; the A-deck's OFCL covers it as a series.

### 1.5 The freshness arm

`ingestion_freshness` grades `nhc_storms_current` on `max(fetch_ts) WHERE product = 'heartbeat'`, stale after the dataset's `stale_after_override` (2 h).

- Reading the view for two rows costs **280 ms**, because it builds every dataset's arm first. So `/storms` reads the newest heartbeat off `tfv_heartbeat` (0.056 ms) and the dataset's ruler, and grades them with the view's own `CASE` (MISSING / `>` stale_after → STALE / FRESH).
- Witnessed at 22:11:41Z: the view's frontier equals the index read (21:54:46.943Z), and both grade FRESH (`freshness_witness.json`, view md5 `d3af42e8…`).

### 1.6 The rows T1 pins, read by hand on Neon

| storm | newest position | best track 18Z | tcvitals 18Z |
|---|---|---|---|
| Isaias | official 013, 21Z: 29.2, −87.0, 100 kt, 959 hPa, MH | 28.5, −87.1, 105 kt, 959 | 28.5, −87.1, 105 kt, 959 |
| Rachel | official 051, 21Z: 23.3, −124.0, 40 kt, 995, TS | 23.0, −124.5, 45 kt, 995 | 23.0, −124.5, 44.7 kt, 995 |
| Simon | official 010, 21Z: 16.2, −104.7, 90 kt, 966, HU | 16.0, −104.7, 90 kt, 966 | 16.0, −104.7, 89.4 kt, 966 |

---

## 2. The contract

**Common to every body:**
- `timestamps`: ISO 8601 UTC (`2026-10-09T12:00:00+00:00`), the repo's one format.
- `longitude: {served, bank_stores, rule}`.
- `not_served` (§2.5) and `nhc_terms`.
- `cache: {state, built_at, age_seconds, ttl_seconds, build_seconds, refreshing}`, plus `Cache-Control: max-age=300`, `X-Cache` and `Age`.

**Units are in field names:** `tau_h`, `lat_deg`, `lon_deg` (signed), `vmax_kt`, `mslp_hpa`, `radii_nm`, `value_pct`, `window_h`, `threshold_kt`, `radius_km`.

**Status codes:**

| case | answer |
|---|---|
| unknown storm_id | **404** `"no storm with storm_id=… in the tropical bank"` |
| malformed storm_id (anything not matching 291's CHECK `^(al\|ep\|cp)[0-9]{2}[0-9]{4}$`, e.g. `AL092026`), bad scope, n not 1–8 | **400**, with no read |
| a known storm with an empty block | **200**, with that block's `*_absence {reason, detail}` |
| DB down, or a read past the 5 s statement timeout | **503** `"db unavailable: …"` |

### 2.1 `GET /api/weather/tropics/storms?scope=active|recent|all`

```
{ scope, scope_rule, count,
  storms: [ { storm_id, basin, number, season, status,
              name, name_absence,                       // the newest entry of `names`
              names: [ {name, from, to} ],              // as stored
              aliases: [ {id, kind, from, to} ],        // as stored
              first_seen_ts, last_seen_ts,              // the bank's sightings
              book_position: { ts, lat_deg, lon_deg, source },
              newest_position: { source, label, valid_ts, lat_deg, lon_deg, vmax_kt,
                                 mslp_hpa, stage, advisory } | null,
              newest_position_absence,
              positions: [ ..one per source read: best_track, nhc_official, tcvitals.. ],
              newest_advisory: { advisory, init_ts, position_valid_ts, position_tau_h } | null,
              newest_advisory_absence } ],
  poll: { newest_heartbeat_ts,
          freshness: { status: FRESH|STALE|MISSING, dataset, stale_after_h, age_s,
                       graded_at, frontier_source, rule, absence } },
  position_rule, advisory_rule, absence, ..common }
```

- **Scopes:**
  - `active` (default): status is not `inactive`, so `active` and `invest`.
  - `recent`: `last_seen` within 7 days, any status.
  - `all`: every storm.
- **`newest_position`** is the latest by `valid_ts` among the newest best-track fix, the newest tcvitals analysis and the newest advisory's own position. Ties go best track, then official, then tcvitals. All three are in `positions`.
- **No storm in scope** is a 200 with `absence.reason = "no_storms_in_scope"`, and its detail names the newest heartbeat and its grade.

### 2.2 `GET /api/weather/tropics/storm?storm_id=`

```
{ storm: { ..identity as above.. },
  observed: { best_track: { source, label, role: "observed", operational: false, points, absence },
              tcvitals:   { source, label, role: "operational", operational: true, points, absence } },
     // points: [ { tau_h, valid_ts, lat_deg, lon_deg, vmax_kt, mslp_hpa, stage, radii_nm } ], time order
  official: { advisory, init_ts, position_valid_ts, position_tau_h, label,
              same_init_advisories: ["012", "012A"], points: [ ..as observed.. ] } | null,
  official_absence,
  cycles: [ { init_ts, sources: [ { source, label, role, n_points, max_tau_h } ] } ],  // oldest first
  cycles_absence, refused, radii_convention, advisory_rule, valid_ts_rule, ..common }
```

### 2.3 `GET /api/weather/tropics/tracks?storm_id=&n=` (n 1–8, default 4; above 8 is 400)

```
{ storm_id, name, n, n_cycles, order, cycle_rule,
  cycles: [ { init_ts,                                             // OLDEST FIRST
              series: [ { source, label, status: known|unknown, role, track, notice,
                          points: [ { tau_h, valid_ts, lat_deg, lon_deg, vmax_kt, mslp_hpa } ] } ],
              official: [ { advisory, init_ts, position_valid_ts, position_tau_h,
                            points: [ ..+ stage.. ] } ],          // every advisory of this synoptic time
              official_absence } ],
  absent: [ { source, label, role, adeck, present_in: [init..], missing_from: [init..] } ],
  absent_rule, absence, refused: [ { source, init_ts, reason: "ensemble_member", detail } ],
  attribution: { ecmwf: { applies_to, notice }, ecmwf_in_consensus: { applies_to, notice } },
  advisory_rule, valid_ts_rule, ..common }
```

- **Series:** one per stored source per cycle, sorted by source.
- **Roles:** `official` (A-deck OFCL), `model`, `ensemble_mean`, `consensus`, `intensity_consensus`, `baseline`, `unknown`.
- **Notices:** every `ecmwf_ifs` and `ecmwf_aifs` series carries the CC BY 4.0 notice with its year and the modifications made. TVCN, HCCA, RVCN and IVCN carry the "may contain ECMWF content" notice, because NHC's ATCF NOTICE puts consensus ECMWF content under CC BY 4.0.

### 2.4 `GET /api/weather/tropics/odds?storm_id=`

```
{ storm_id, name, source: "nhc_pws", source_label,
  issuance: { issued_ts, advisory } | null,
  places: [ { place_id, coordinates: null,
              thresholds: [ { threshold_kt, radius_km,
                              windows: [ { window_h,
                                           cumulative: { value_pct, below_1pct, scored },
                                           onset:      { value_pct, below_1pct, scored } } ] } ],
              thresholds_absent: [ { threshold_kt, reason: "not_printed", detail } ] } ],
  absence, values_rule, windows_rule, places_rule,
  place_coordinates: { served: false, stop_p, pantry_would_bank },
  odds_not_served, ..common }
```

- **`below_1pct` is carried as itself.** Where it is true, `value_pct` is **null**: the bank stores 0 there as a placeholder for NHC's "X", which is not a value NHC printed.
- **Order and print thresholds:**
  - Places come in the bank's insertion order, which is NHC's printed order.
  - A threshold NHC did not print is named in `thresholds_absent`: it is below NHC's print threshold, not 0.
- **Windows** are 12, 24, 36, 48, 72, 96 and 120 h. At 12 h NHC prints one number, which the bank stores as both onset and cumulative.

### 2.5 `not_served`, in every body

| key | why |
|---|---|
| `ensemble_members` | ENS 51, AIFS-ENS 52, GEFS AP01–AP30 and AC00 are R2 parquet only, with no member column in Neon. This API reads no R2. |
| `cone_and_warnings` | banked as files in R2 (5-day zip, cone and WW KMZ), not rows |
| `storms_before_2026` | nothing before 2026 is parsed; lane d091671 |
| `model_scores` | no track-error table; every odds row is `scored = false` |
| `place_coordinates` | STOP-P (§3) |

### 2.6 Why four routes, not one or two

- **The page opens a storm with `/storm`, `/tracks` and `/odds` in parallel.** That is one round trip of latency, and stepping the player never fetches (rule 5): `/tracks` carries every cycle.
- **They stay apart for three reasons:**
  1. `/tracks` is 20–60× the others (335–394 KB raw at n = 8) and is memoised per n.
  2. A slow or failed tracks read must not blank the storm card or the odds table.
  3. `/storms` is the one route polled while the page sits open.

### 2.7 The memo

- **Settings:** 300 s fresh, single-flight, **never served stale** (`allow_stale` false), keyed per scope, per storm_id, or per `(storm_id, n)`.
- **Why 300 s:** NHC's official poll is every 15 min and advisories land at 03/09/15/21Z. So a banked advisory reaches the page within 5 minutes, and at most 20 after NHC posts it.
- **Why never stale:** every build is milliseconds (§4), so a miss waits on a fast read rather than serve a body up to 15 minutes older.
  - This also leaves d091608's exact set of D-09-25-138-bounded memos unchanged; that test file is untouched.

---

## 3. STOP-P's answer

**What was found:** no table the API can read holds a PWS place's coordinates or any name beyond the printed `place_id`.

**What the API does:**
- It serves `place_id` verbatim, with `coordinates: null` on every place.
- `place_coordinates` says why, and `not_served.place_coordinates` says it in every body.
- It embeds no gazetteer: `test_T6_stop_p_*` fails if any bank place name appears in the code outside two prose examples.

**What pantry would bank:** `tropical_pws_places`, keyed by `place_id` exactly as NHC prints it, with:
- basin;
- a display name;
- latitude and longitude;
- the source of the coordinates and when they were read.

The API would then join on `place_id`, and the grid points (`GFAM 290N 850W`) could be filled from their own names by pantry, not here.

---

## 4. Timings and plans (`docs/receipts/tropics-api-d091673/plans.md`)

| route | reads (Neon, warm) | total |
|---|---|---:|
| /storms | heartbeat 0.056 ms + storms with 2 LATERAL LIMIT 1 each 0.135 ms + newest official 0.150 ms | ≈ 0.34 ms |
| /storm Rachel | storm 0.03 + observed 1.02 + official 0.15 + cycle census 10.3 ms | ≈ 11.5 ms |
| /tracks Rachel n = 8 | storm 0.03 + tracks 3.26 ms (115 buffers) | ≈ 3.3 ms |
| /odds Isaias | storm 0.03 + odds 0.357 ms | ≈ 0.4 ms |

- **The cycle census.** As a plain GROUP BY it seq-scans the whole points table (10.8 ms, 868 buffers): Rachel is 64 % of it, and the cost grows with every backfilled storm. The shipped census probes `ttp_storm_init` once per cycle (57 probes), then reads one index range per cycle: 10.3 ms, growing with the storm alone.
- **`/tracks`** uses the same loose index scan, which stops after n probes; Rachel's 48 older cycles are never touched.
- **`tropical_storms`** is one page and is read whole.
- **At 31× (1.07 M points, locally)**, no statement seq-scans the points, odds or heartbeat table (`test_T10_*_at_31x`).

---

## 5. Body sizes (`sizes.json`; compact JSON as served, gzip −6)

| storm | /tracks n = 1 | n = 4 | n = 8 | /storm | /odds |
|---|---|---|---|---|---|
| Isaias | 14.9 KB / 3.0 | 174 KB / 18.1 | 388 KB / **38.1** | 47.7 KB / 4.3 | 35.2 KB / 2.8 |
| Rachel | 14.2 KB / 2.9 | 140 KB / 14.1 | 335 KB / **31.3** | 136 KB / 6.2 | 22.4 KB / 2.5 |
| Simon | 16.2 KB / 3.1 | 175 KB / 17.8 | 394 KB / **38.2** | 47.5 KB / 4.5 | 24.1 KB / 2.6 |

`/storms` is 8.8 KB / 2.3 KB. The largest gzip is 38 KB, against STOP-Z's 500 KB. The API's existing GZip middleware compresses on the wire.

---

## 6. What pantry would need to bank

- **Members.** A per-request read needs rows in Neon, not R2. Today's cycle is 44,754 parquet points for three storms, so a member table would make a /tracks n = 8 body about 30× larger. Bank a **per-cycle summary** instead:
  - the member mean and spread per tau;
  - strike probability on a grid or at the PWS places;
  - with `n_members` and `scored = false`.
  - `tropical_place_odds` already has the columns for the place frequencies (`ecmwf_ens`, `n_members`).
- **The cone and warnings.** One row per advisory per kind (cone, watch, warning) with the geometry as GeoJSON in WGS84. The source `.prj` is GCS_Sphere, and the bank should say so.
- **Place names:** §3.
- **IVCN:** store no position for an intensity-only aid. `lat` and `lon` are NOT NULL, so either relax that for IVCN or keep IVCN out of the points table. Today it is 1,230 rows at 0°, 0°.
- **tcvitals:** put the storm name somewhere other than `stage`.

---

## 7. What the dashboard lane must know

**What to fetch:**
1. **The list.** Poll `/storms` (300 s memo). Draw a marker per storm at `newest_position`, and print its `source` and `valid_ts`.
   - Print `poll.freshness.status` beside the list; STALE means the bank has not polled NHC for over 2 h.
   - An empty list has `absence`; print its detail, which carries the poll time.
2. **Opening a storm.** Fetch `/storm`, `/tracks?n=8` and `/odds` in parallel. Keep one `n`, because it is the memo key.

**What to draw:**
- **Observed:** the best track as the path. Draw tcvitals as operational dots, labelled as such. `radii_nm` uses NE/SE/SW/NW nautical miles.
- **Official forecast:** `official.points` with radii.
- **Spaghetti:**
  - Draw `cycles[k].series` by `role`, and filter by role, not by source string.
  - **Never draw a series with `track: false`** (IVCN): plot its intensity only.
  - Two sources may be one model through two feeds (`same_model_as`); do not hide one silently.
- **Player:**
  - Step `cycles[0..n−1]` locally, oldest first; the newest is last.
  - Each cycle's `official` lists every advisory of that synoptic time, in order; the last is the one in force latest.
  - The newest cycle often holds only the early aids. `absent[]` says which late models are missing, and the page should say they have not landed yet.
- **Odds:**
  - Print `below_1pct` as "<1 %", never 0.
  - A missing threshold or place is below NHC's print threshold, not 0.
  - Places have no coordinates: draw a table, not a map.

**What to state as absent:**
- every `*_absence`;
- `absent[]` and `thresholds_absent`;
- `not_served`: members, cone and warnings, pre-2026 storms, scores, place coordinates.

**Attribution:**
- Print each series' `notice` wherever an ECMWF series, or a consensus aid that may contain ECMWF content, is drawn.
- Print `nhc_terms` once.

**Longitude:** served signed, so a storm crossing the antimeridian jumps from 180 to −180, and the map must unwrap it.

**`stage`:** on tcvitals points it is a name, not a stage.

**Vectors:** `tests/fixtures/tropics_d091673/bodies/*.json`, 11 bodies served at 2026-10-09 22:00Z, without `cache`. `test_V_*` holds the routes to them byte for byte.

---

## 8. Tests (`tests/test_tropics_d091673.py`, 87)

**The fixture.** The whole bank is in `tests/fixtures/tropics_d091673/bank/`:
- every row of the three point, odds and storm tables, plus the heartbeat and dataset rows the routes read;
- each row as Postgres's own `row_to_json` text, in key order, cut at one instant, 812 KB gzipped.

| file | rows | sha-256 (Neon's, re-checked on every load) |
|---|---:|---|
| storm | 3 | `5df912f12d3346ad3aca101a253ead4da9cba1eb3288ea6166dc3071d9dc4b22` |
| point | 34,518 | `5b3d8c8beb36bcefdcaa99f75396033fb2d20f1e973e291b5086efd8987ded9a` |
| odds | 2,674 | `061b04ba92aa063c87103b88916447a6837aa44891742446446bb50ea16b96ac` |
| heartbeat | 31 | `e1cb8dce0c373b2100fea26fc178f1b9ffc9436968dec98a062370abae06e1bc` |
| dataset | 2 | `96f03585ac88505918e9f2aaecb0ca30201f0c44f2b48e002be69732ba4fa534` |

**Postgres.** The route tests load the bank into a throwaway local Postgres 16 built from migration 291's own table DDL, with every CHECK and index (`ddl_291_tables.sql`). The routes' statements then run unchanged.
- Constructed cases (the 008a/008A/8 ordering, two storms with one name, members, unknown sources, timeouts) run on the FakePool.
- PG tests skip by name where there is no initdb.

**Red, then green:**
- **Red on main** (`red_on_main.txt`): with no `tropics.py`, collection fails. With `tropics.py` but no routes, 72 tests fail and 15 module tests pass.
- **Green here:** 87 passed. The whole suite is 2,734 passed.

**The rehearsal** (`reds.txt`, `tests/rehearse_tropics_d091673.py`): 21 one-line breaks, every one red, and the tree restored byte for byte (sha `e300f0d0…` before and after).

| rule | breaks |
|---|---|
| 1 | unknown source given a known label; a label worded outside SOURCES; positions rounded; valid_ts "corrected" by 3 h |
| 2 | storms sharing a name merged |
| 3 | the member filter dropped |
| 4 | no storms without absence; unknown storm as 200; a missing threshold not named |
| 5 | only the newest cycle; cycles newest first |
| 6 | ECMWF notice dropped |
| 7 | no statement timeout; memo key drops n; n trimmed; the census loses its index; stale serving |
| shape | advisories by string; east-positive lon unsigned; below_1pct as 0; `>=` for the view's `>` |

---

## 9. What this brief got wrong

1. **The counts moved.** The brief has 33,315 points and 1,806 odds. At the cut there were 34,518 and 2,674, and the bank keeps growing.
2. **"Advisory strings arrive as `008a`, `008A` and `8` in one file."** They arrive that way at NHC, but the bank normalises them. Neon holds only `NNN` and `NNNA`, so T4's three spellings are a constructed case.
3. **"The official forecast is the newest advisory by init_ts" does not decide.** An intermediate carries its parent's `init_ts`. The tie is broken by the advisory's own position time (§1.3).
4. **"Report whether the bank corrected VALIDTIME."** It did, and a CHECK holds it. "Correct nothing in the API" is therefore all there is to do.
5. **The point shape `{tau, valid_ts, lat, lon, …}` contradicts "units in field names".** The latter won: `tau_h`, `lat_deg`, `lon_deg`.
6. **"freshness read from the bank" via the view would cost 280 ms per build.** The frontier is read off its index and graded by the view's rule, held equal to the view by a witness.
7. **"Which A-deck aids are early or late versions of one model."** The bank keeps no interpolated aid, so there is none. The real duplicates are cross-feed (HFSA/hafs_a, HFSB/hafs_b, OFCL/nhc_official, NVGM/NGX), and they are not identical.
8. **IVCN is listed among the aids to draw.** It has no track: every row is at 0°, 0°.
9. **"First and last seen"** are the bank's sightings, not the storm's life. Rachel's best track starts 13 days before her `first_seen`.
10. **"The official forecast in force at each cycle"** needed a definition: the same synoptic time, every advisory of it. The GIS official exists only since 10-09 06Z; older cycles say so, and OFCL covers them as a series.
11. **"T10 bytes for Rachel."** Rachel is the longest-lived storm but not the largest body. Simon's and Isaias' n = 8 are larger (394 and 388 KB against 335 KB raw).
12. **"T5 a constructed ensemble-source row is refused by name."** The bank's own CHECK refuses to store one, and T5 shows that too. The API's refusal is tested on a constructed read.
13. **D-09-25-138's bounded stale serve was not used.** These memos never serve stale, because the reads are milliseconds. That keeps d091608's exact-set test untouched, as "existing suite untouched" requires.

---

## 10. Not done, or for later

- **Best-track revisions.** `uniq_tropical_track_points` keeps the first-written fix (`ON CONFLICT DO NOTHING`); later revisions live only in the banked decks. Not measured here: whether any fix was revised.
- **ROCI and the outermost closed isobar** are not in Neon (d091664 §9). Not served.
- **This lane reached Neon only through the connector.** The bank was read in nine statements (one for the storms and dataset rows and the shas, seven for the points, one for the odds and heartbeats; up to two million characters each) and reassembled by `bank_from_neon.py`'s method, every sha checked against Neon's.

*Sources:*
- this repo: `tropics.py`, `main.py` (the `THE TROPICS PAGE` section);
- tests: `tests/test_tropics_d091673.py`, `tests/load_bank_d091673.py`, `tests/rehearse_tropics_d091673.py`, `tests/fixtures/tropics_d091673/`;
- receipts: `docs/receipts/tropics-api-d091673/` (`plans.md`, `explains.sql`, `sizes.json`, `red_on_main.txt`, `reds.txt`, `bank_bodies.py`, `render_explains.py`);
- pantry: migrations 291 and 293, `tropical/points.py`, `tropical/storms.py`, `tropical/parsers/`, `tropical/nhc.py`, `docs/handback_2026_10_08_tropics_bank.md`.
