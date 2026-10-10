# d091679 plan receipts

Neon production (`fancy-block-96153928`), read-only, 2026-10-10 00:20–00:35Z.
`explains.sql` holds each statement: the pinned SQL in `cpc_outlooks.py` with
the params after its tag as literals. `plans_raw.txt` holds every plan line
verbatim, cut from the connector's own results by `extract_plans.py`, which
matches each statement character for character. `test_T8_the_plan_receipts_ran_the_pinned_sql`
rebuilds each statement from the pin.

| tag | read | params | rows | execution | shape |
|---|---|---|---:|---:|---|
| C01 | CURVES_SQL | 610temp, KSAN, n=1 | 6 | 1.253 ms | `idx_coc_issued` (1 row) → `uniq_coc_row` window row → verdict cell on `uniq_ccv_cell` → view rows on `uniq_coc_row` (issued_date as an equality) |
| C02 | CURVES_SQL | 814temp, KSAN, n=1 | 8 | 0.922 ms | the same |
| C03 | CURVES_SQL | 814temp, pnw/population, n=14 | 112 | 8.112 ms | `idx_coc_issued` reads 1,769 entries to find 14 issuances; per issuance one window row and the view's 8 rows. The view's own `newest` CTE (a DISTINCT ON over all 720 verdict rows) runs once per issuance: 14 × 0.28 ms |
| C04 | CURVES_SQL | 610temp, KDEN (no curve), n=14 | 14 | 0.829 ms | 14 window-row probes find nothing; nothing walks |
| P01 | PLACES_SQL | — | 720 | 3.555 ms | seq scan of 51 pages (the table is 720 rows); the version in force by one LATERAL … LIMIT 1 per method_hash |
| P02 | PLACES_NEWEST_SQL | both products | 34 | 0.325 ms | one `idx_coc_issued` probe per product, then `uniq_coc_row` on (product, issued_date, day_index NULL) |
| O01 | OUTLOOK_FEATURES_SQL | all 12 codes | 108 | 10.906 ms | one `idx_cpc_features_product_issued` probe per code (an unbanked code stops at once), then `uniq_cpc_outlook_features_layer`; first read, 33 pages from storage |
| O02 | OUTLOOK_VINTAGE_SQL | all 12 codes | 12 | 0.191 ms | one `idx_cpc_outlook_vintage_product_issued` probe per code |
| O03 | OUTLOOK_GEOMETRY_SQL | wk34temp, wk34prcp | 29 | 6.129 ms | as O01; 1.47 MB of GeoJSON sorted in memory |

**Superseded (C00b in `plans_raw.txt`).** The first CURVES_SQL joined the view
by a plain LATERAL. The planner flattened it into a hash join on
`(issued_date, method_version)`, and the view's rows were found by a bitmap
scan of every row of the place in the product: 12,104 rows, **112 ms warm**
(and 1,086 ms on a cold cache, an earlier `EXPLAIN` of the same joins with a
shorter select list, not kept here). `OFFSET 0` inside the LATERAL fences it: C03
reads 8 rows per issuance by `uniq_coc_row` with issued_date as an equality.

**Named risk: the view's verdict CTE grows.** `v_cpc_curves_drawable` picks
the newest backtest_version per method_hash with `DISTINCT ON` over the whole
of `cpc_curve_verdicts`, and that sort runs once per issuance in a vintages
read (C03: 14 loops × 720 rows). The verdict writer adds 720 rows a week and
keeps old versions, so in a year it is ~37,000 rows: projected ~10 ms a sort,
~150 ms for n = 14. Under the 2 s line, but linear. The fix is pantry's
(handback §8): the view's `newest` as one LATERAL … LIMIT 1 per method_hash on
`idx_ccv_version`, as this repo's d091551 rule asks of our own reads.
