# cpc_outlooks_d091679 — production rows, read from Neon 2026-10-10 00:18–00:21Z

Each `*.json.gz` is gzip of the exact `t` text of one read-only statement
(`coalesce(json_agg(row_to_json(q)), '[]')::text` over the read), plus a
newline. `manifest.psv` is `file | rows | sha256 | length`, the sha-256 and
length computed by Neon over that same text; `verify.py` re-checks every file,
and the tests check each file as they load it.

| file | read |
|---|---|
| curves_{610temp,814temp}_ksan.json | `CURVES_SQL`, station USW00023188 (KSAN, drawable both products), n = 14 |
| curves_{…}_pnw_population.json, …_pnw_load_share_365d.json | `CURVES_SQL`, region pnw at each weighting, n = 14 |
| curves_{…}_kden.json | `CURVES_SQL`, station USW00003017 (KDEN: 24 base years, no curve written), n = 14 |
| places_verdicts.json | `PLACES_SQL`: the 720 cells of the version in force |
| places_newest.json | `PLACES_NEWEST_SQL`: the 34 window rows of each product's newest written issuance |
| outlook_features.json | `OUTLOOK_FEATURES_SQL`, all 12 product codes |
| outlook_vintage.json | `OUTLOOK_VINTAGE_SQL`, all 12 product codes |
| outlook_geometry_wk34.json | `OUTLOOK_GEOMETRY_SQL`, wk34temp and wk34prcp |
| handread_view_{product}_ksan.json | the hand read for T1: `SELECT * FROM v_cpc_curves_drawable` at KSAN, the product's max(issued_date) |
| handread_window_{product}_ksan.json | the hand read for T1: KSAN's window row (identity, reading, history, notes) |
| count_verdicts.json | the count query for T5: cells and drawable cells per place and product in the newest version |

The eight curve files were read in one statement (the pinned SQL inside a
LATERAL over the eight parameter sets); the others in a second, the geometry
in a third. Between them the bank's newest 14 issuances and its one verdict
version did not move: the backfill was writing 2023 issuances at the time.
The fake pool slices a curve file to the newest `n` issuances, as the read's
`LIMIT n` does.
