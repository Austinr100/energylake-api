# d091635 — the asset page's reads on production (EXPLAIN ANALYZE, BUFFERS)

Measured 2026-10-07 ~01:20Z through the Neon connector (read-only), Energylake
project, default branch. Pantry migration 280 is NOT applied: the catalog read
returns no row, and the route serves the curve with `drivers.absence`.

| read | plan | rows | buffers | time |
|---|---|---|---|---|
| `hours_sql(())` wind 57514 hrrr_gfs | Index Scan `implied_gen_site_latest_pkey`, Index Cond (tech, plant_code, model) | 240 | shared read=11 | 21.9 ms (cold) |
| `SOLAR_UNITS_SQL` solar_pv 58388 | Index Scan `implied_gen_sites_pkey`, Index Cond (tech, plant_code) | 7 | shared read=3 | 1.0 ms |
| `wind_site_sql(())` 57514 | PK lookup `implied_gen_wind_sites_pkey` (plant_code) | 1 | — | — |
| `COLUMNS_SQL` (wind's five names) | catalog index scans (pg_class relname, pg_attribute relid) | 0 | hit=31 read=2 | 1.3 ms |
| `SEARCH_SOLAR_SQL` q=kern | Seq Scan `implied_gen_sites` (1,914 rows) → HashAggregate → top-N | 20 of 148 | hit=4 read=117 | 10.3 ms |
| `SCORES_SQL["hub"]` | the outlook routes' statement, unchanged (docs/receipts/solar-outlook-api-d091568) | 13 | — | — |

The search is a scan BY DESIGN and bounded: both registries are refreshed in
full by their writers and do not grow with time (1,914 solar units, 323 wind
plants). It is behind a 300 s memo per lower-cased query.

Nothing here is a `max()` over a dataset or a `DISTINCT ON`: the newest cycle
is whatever implied_gen_site_latest holds for the plant's key (it holds the
newest cycle only), and the route keeps the newest `init_ts` among those rows
in Python, counting any it dropped (`run.hours_of_other_cycles_dropped`).

The banked reads behind the tests and the dashboard's vector are
`tests/fixtures/asset_page_d091635/bank_2026_10_07.json`, produced by
`bank.sql` beside it (every statement the routes run, rendered with its
parameters, in ONE statement so the snapshot is consistent). The payloads are
`asset_solar_58388.json`, `asset_wind_57514.json`, `assets_solar_star.json`,
`assets_ocotillo.json` here, written by `bank_payloads.py`.
