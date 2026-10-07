# d091644 receipts: reads, plans, bytes

**Measured:** Neon project `fancy-block-96153928`, production branch, 2026-10-07 16:43–16:50Z. Every statement is an `EXPLAIN (ANALYZE, BUFFERS)` of the routes' own SQL (`vintages.VINTAGES_SQL`, `solar_outlook.CALIBRATION_SQL`, `vintages.DD_VINTAGES_SQL`), with literals in place of the parameters.

- The SQL as run is in `explains.sql`.
- Every plan line, verbatim, is in `plans_raw.txt`.
- Nothing was written to Neon.
- No statement came near the 5 s stop.

**"First" is not cold here.** These reads ran after this lane had banked the same CISO and pnw rows (`tests/fixtures/vintages_d091644`), so the CISO "first" runs found most pages already in cache. To get a real first read, G13/G14 read **AZPS**, an area this session had not touched: 179 blocks were read from storage. The recon's first figures (19.3 ms at N = 4, 96.6 ms at N = 28) remain the nearest thing to a compute-cold read.

## Implied generation: the curve read (recon §2.1 a) and the gate's line lookup

| tag | read | n | rows | buffers | time | planning |
|---|---|---:|---:|---|---:|---:|
| G01 | solar CISO, first | 4 | 960 | 28 hit / 37 read | 1.36 ms | 0.38 ms |
| G02 | solar CISO, warm | 4 | 960 | 65 hit | **0.96 ms** | 0.26 ms |
| G03 | solar CISO, first | 12 | 2,880 | 199 hit / 18 read | 2.89 ms | 0.26 ms |
| G04 | solar CISO, warm | 12 | 2,880 | 217 hit | **2.97 ms** | 0.26 ms |
| G05 | solar CISO, first | 28 | 4,632 | 424 hit | 4.52 ms | 0.26 ms |
| G06 | solar CISO, warm | 28 | 4,632 | 424 hit | **4.49 ms** | 0.26 ms |
| G07 | wind CISO, first | 4 | 960 | 32 hit / 43 read | 1.28 ms | 0.30 ms |
| G08 | wind CISO, warm | 4 | 960 | 75 hit | **1.12 ms** | 0.32 ms |
| G09 | wind CISO, first | 12 | 2,880 | 193 hit / 9 read | 3.43 ms | 0.26 ms |
| G10 | wind CISO, warm | 12 | 2,880 | 202 hit | **2.68 ms** | 0.26 ms |
| G11 | wind CISO, first | 28 | 4,110 | 374 hit | 4.15 ms | 0.26 ms |
| G12 | wind CISO, warm | 28 | 4,110 | 374 hit | **4.15 ms** | 0.25 ms |
| G13 | solar **AZPS**, first (cold proxy) | 28 | 4,632 | 190 hit / **179 read** | **36.7 ms** | 0.27 ms |
| G14 | solar AZPS, warm | 28 | 4,632 | 369 hit | 4.47 ms | 0.26 ms |
| G15 | solar CISO lines, the 18 ids at n = 28, first | — | 18 | 1 hit / 6 read | 0.90 ms | 0.10 ms |
| G16 | the same, warm | — | 18 | 7 hit | 0.06 ms | 0.11 ms |
| G17 | wind CISO lines, the 10 ids at n = 28, first | — | 10 | 3 hit / 3 read | 0.14 ms | 0.10 ms |
| G18 | the same, warm | — | 10 | 6 hit | 0.09 ms | 0.10 ms |

**The plan (G01–G14)** is the recon's: `Recursive Union` → `Index Only Scan Backward using implied_gen_area_hourly_pkey` (one probe per init), then `Nested Loop` → `Index Scan using implied_gen_area_hourly_pkey` (the 5-column prefix plus `init_ts = w.init_ts`), then `Sort`.
- There is no sequential scan.
- Buffers grow with n: about 15 per issuance held.
- G15–G18 are `Index Scan using implied_gen_calibration_pkey` on `calibration_id = ANY(...)`.

**The join I did not build.** Joining the lines into the curve read, so that it is one statement, was measured first: 108.3 ms first at n = 28. The planner chose a `Seq Scan on implied_gen_calibration` (762 rows, 21 buffers) feeding a hash join. The route instead runs the outlook's own line statement after the curve read, inside the same transaction: a PK lookup of at most 18 ids in 0.06–0.9 ms. See the handback, §6 item 1.

## Degree days: one read per source, every held issuance (pnw / population)

| tag | source | issuances | rows | buffers | first | warm | planning |
|---|---|---:|---:|---:|---:|---:|---:|
| D19/D20 | GFS | 12 | 192 | 5,064 hit | 13.3 ms | **12.1 ms** | 1.2–1.3 ms |
| D21/D22 | IFS | 12 | 192 | 5,223 hit | 11.5 ms | **11.5 ms** | 1.2 ms |
| D23/D24 | AIFS | 11 | 176 | 4,854 hit | 11.2 ms | **11.6 ms** | 1.3 ms |
| D25/D26 | gridpoints_raw (NWS folds) | 5 | 43 | 1,231 hit | 8.4 ms | **2.9 ms** | 1.3–1.4 ms |

**STOP-V (500 ms warm) is not reached.** The slowest warm read is 12.1 ms.

**The cost is the recon's ~400 buffers per issuance held, now ~420:**
- GFS: 5,064 / 12.
- IFS: 5,223 / 12.
- AIFS: 4,854 / 11.

Nearly all of it is two per-member probes, each run once for every (issuance × target date × member) row: `idx_sddf_product_target` (~2,600 hits) and `station_normals_daily_pkey` (2,304 hits).

**Linear projection, not measured:** about 1 ms warm per issuance held. At two runs a day per model, a read reaches the 500 ms line at about 500 issuances, roughly eight months from 10-02. A cold compute will cross it sooner. The named fix is unchanged from the recon: a spine keyed on `(region, weighting, source_product, fold_key)`, which belongs to a pantry lane.

**Pool use.** The four reads run concurrently, so a cold build holds four of the pool's five connections for about 12 ms. The region board already holds three.

**The view's own small tables are the only sequential scans:**
- `degree_day_region_weights`: 39 rows.
- `station_degree_days_forecast_folds`: never executed for a model source; executed for gridpoints_raw.

## Bytes (as served: FastAPI's compact JSON, the real routes, production rows)

Made by `sample.py`: the routes are served through `TestClient` over the md5-checked bank. n = 4 and 12 are the bank's newest 4 and 12 issuances, which is what `VINTAGES_SQL` returns at those n (PG test `test_PG_V4`). gzip is level 6.

| route | n | array entries (hours or days) | raw bytes | gzip bytes |
|---|---:|---:|---:|---:|
| `/api/generation/solar/vintages` ba/CISO | 4 | 960 | 8,878 | 2,106 |
| | 12 | 2,880 | 26,183 | 5,459 |
| | 28 | 4,632 | 39,596 | 8,544 |
| `/api/generation/wind/vintages` ba/CISO | 4 | 960 | 9,935 | 3,127 |
| | 12 | 2,880 | 28,965 | 8,483 |
| | 28 | 4,110 | 37,907 | 11,511 |
| `/api/weather/dd/forecast/regions/vintages` pnw/population | all 40 | 603 days | 15,099 | 2,526 |

These include the `cache` block and every key. The recon's 26.7 KB raw / 8.2 KB gz at N = 28 was the `reg` array alone; with `cal`, the keys and the stamps the body is 39.6 KB / 8.5 KB gz. Wind compresses worse than solar because it has no run of night zeros.

## The banked bodies (the dashboard lane's vectors, D-09-05-T)

| file | what it is |
|---|---|
| `body_solar_ciso_n28.json` | `GET /api/generation/solar/vintages?area_kind=ba&area=CISO` (n = 28), 2026-10-07: inits 09-20 12Z → 10-07 06Z |
| `body_wind_ciso_n28.json` | `GET /api/generation/wind/vintages?area_kind=ba&area=CISO`, inits 09-18 06Z → 10-07 12Z |
| `body_dd_pnw_population.json` | `GET /api/weather/dd/forecast/regions/vintages?region=pnw&weighting=population` |

- The bodies are indented for reading; the wire form is compact.
- `tests/test_vintages_d091644.py` holds each one, `cache` block aside, equal to what the route serves over the bank (`test_V6_the_banked_body_is_what_the_route_serves`, `test_dd_banked_body_is_what_the_route_serves`).
