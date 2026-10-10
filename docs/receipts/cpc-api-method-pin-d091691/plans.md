# d091691 plan receipts

Neon production (`fancy-block-96153928`, PostgreSQL 17.11), read-only,
2026-10-10 ~14:05–14:20Z. `make_explains.py` writes `explains.sql`: each read
that names `cpc_outlook_curves`, `cpc_curve_verdicts` or `v_cpc_curves_drawable`,
with d091679's params for it, twice:

- **Axx** is the pinned SQL (`cpc_outlooks.py`);
- **Bxx** is main 891de2e's SQL (`../cpc-outlooks-d091679/pinned_sql.json`), re-run in the
  same session, so before and after are read on the same bank.

`extract_plans.py` cuts every plan line verbatim from the connector's own
results, matching each statement character for character. `plans_raw.txt` holds
every run, in order. The first run of a statement can be cold: Neon reads pages
from storage and sets hint bits on rows written last night (`read=`, `dirtied=`).
The A statements that came back cold were run again; both runs are kept.
`test_P5_*` rebuilds every statement from the pin and holds the rule below.

The bank at the time (counted 2026-10-10 ~14:00Z): no `cpc_curves_v2` row in
either table. 610temp held 183,600 rows and 814temp 244,664, both with newest
issuance 10-09. `cpc_curve_verdicts` held 720 rows.

| read | params | d091679 | B (main, now) | A cold | A warm | A warm / d091679 |
|---|---|---:|---:|---:|---:|---:|
| 01 CURVES_SQL | 610temp, KSAN, n=1 | 1.253 ms | 2.886 ms | 6.441 ms | **1.412 ms** | 1.13× |
| 02 CURVES_SQL | 814temp, KSAN, n=1 | 0.922 ms | 16.730 ms | 4.002 ms | **1.390 ms** | 1.51× |
| 03 CURVES_SQL | 814temp, pnw/population, n=14 | 8.112 ms | 15.674 ms | 876.393 ms | **12.213 ms** | 1.51× |
| 04 CURVES_SQL | 610temp, KDEN (no curve), n=14 | 0.829 ms | 2.648 ms | 6.327 ms | **1.330 ms** | 1.60× |
| 05 PLACES_SQL | — | 3.555 ms | 3.655 ms | — | **3.693 ms** | 1.04× |
| 06 PLACES_NEWEST_SQL | both products | 0.325 ms | 0.183 ms | 2.799 ms | **0.181 ms** | 0.56× |

**STOP-P is not hit.**
- Warm, every pinned read is within 1.6× of d091679's timing.
- The cold runs are storage reads, not the predicate. A03's cold run read 81
  pages and dirtied 37, and spent 29.7 ms per window-row probe on a page fetch.
  Its warm run is the same plan with every page a hit.
- No plan scans `cpc_outlook_curves`.
- The only whole-table scans are of `cpc_curve_verdicts`: 720 rows, 51 pages.
  Main's plans scan it the same number of times, read for read:
  - the `nv` lateral and the view's own `newest` CTE in CURVES_SQL;
  - all three references in PLACES_SQL.

  The pin adds a filter to those scans and no new scan.

**What the predicate changed in each plan:**

| site | main (B) | pinned (A) |
|---|---|---|
| CURVES_SQL `iss` (the n newest issuances) | Index Only Scan `idx_coc_issued` (product) | Index Only Scan **Backward `uniq_coc_row`**, Index Cond (product, method_version); the same 1 / 1,769 / 1,327 entries read; heap fetches unchanged |
| CURVES_SQL `w` (the window row) | `uniq_coc_row` on (product, issued_date, place_kind, place, weighting, day_index) | `uniq_coc_row` on the **full key**, method_version included |
| CURVES_SQL `nv` (the verdict in force) | Seq Scan `cpc_curve_verdicts`, Filter method_hash | the same scan, Filter method_hash AND method_version (Memoize on the hash at n=14, as before) |
| CURVES_SQL `vv` (the cell) | `uniq_ccv_cell`, Filter method_hash | `uniq_ccv_cell`, Filter method_hash AND method_version |
| CURVES_SQL `dv` (the view rows) | `uniq_coc_row` with `method_version = w.method_version` | `uniq_coc_row` with `method_version = 'cpc_curves_v1'`, under a One-Time Filter `w.method_version = 'cpc_curves_v1'` (the view is not read when there is no v1 window row) |
| PLACES_SQL | HashAggregate over a Seq Scan; LIMIT 1 per hash over a Seq Scan; Seq Scan of the cells | the same three scans, each with Filter method_version |
| PLACES_NEWEST_SQL `iss` | Index Only Scan `idx_coc_issued` per product | Index Only Scan Backward `uniq_coc_row`, Index Cond (product, method_version) |
| PLACES_NEWEST_SQL `c` | `uniq_coc_row` (product, issued_date, day_index) | `uniq_coc_row` (product, issued_date, day_index, method_version) |

**Named risk: the `iss` walk once v2 is written.** The pinned `iss` walks
`uniq_coc_row` backward within the product and filters method_version in the
index, with no heap visit. Two cases:
- **v2 beside v1 at the same issuances:** the walk reads about twice the entries.
  Today that is 1,769 rows at n = 14, so it would read about 3,500 index tuples
  of the same pages.
- **v2-only issuances, if the v1 writer stops:** the walk passes about 420
  index tuples per day before it reaches a v1 row.

Both are bounded by days, not by the table. The flip lane reverses this: once
pinned to v2, it walks past any v1-only days.
