# d091693 plan receipts

Neon production (`fancy-block-96153928`), read-only, 2026-10-11 ~13:49–13:56Z.
`make_explains.py` writes `explains.sql`: each read that names
`cpc_outlook_curves`, `cpc_curve_verdicts` or `v_cpc_curves_drawable`, with
d091679's params for it (the six d091691 re-took), twice:

- **Axx** is the v2-pinned SQL (`cpc_outlooks.py`, this branch);
- **Bxx** is main a10b6f2's v1-pinned SQL (`../cpc-api-method-pin-d091691/pinned_sql.json`):
  production today, re-run in the same session on the same bank.

`extract_plans.py` cuts every plan line verbatim from the connector's own
results, matching each statement character for character (12 statements, 22
plans). `plans_raw.txt` holds every run, in order. Two runs were contended:
the first batch (A01–A06 run 1) went out in parallel, and so did A03 run 2,
A04 run 2 and B03 run 1 (A03 run 2: 454 ms with every page a hit; the
`newest` CTE took 2.6 ms a loop instead of 0.25). Every later run was
sequential, and the last run of each is the one compared. `test_V5_*`
rebuilds each statement from the code and holds the rules below.

The bank at the time: `cpc_outlook_curves` v1 3,599 issuances, v2 3,601;
`cpc_curve_verdicts` 1,440 rows (720 per version).

| read | params | d091679 | d091691 A | B now (production) | A first | **A warm** | A / d091691 | A / B |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 01 CURVES_SQL | 610temp, KSAN, n=1 | 1.253 | 1.412 | 4.212 | 6.582 | **2.254** | 1.60× | 0.54× |
| 02 CURVES_SQL | 814temp, KSAN, n=1 | 0.922 | 1.390 | 5.365 | 2.255 | **1.422** | 1.02× | 0.27× |
| 03 CURVES_SQL | 814temp, pnw/population, n=14 | 8.112 | 12.213 | 77.359 | 31.991 | **31.345** | 2.57× | 0.41× |
| 04 CURVES_SQL | 610temp, KDEN, n=14 | 0.829 | 1.330 | 5.786 | 25.336 | **24.026** | **18.06×** | 4.15× |
| 05 PLACES_SQL | — | 3.555 | 3.693 | 4.228 | 4.577 | **4.208** | 1.14× | 1.00× |
| 06 PLACES_NEWEST_SQL | both products | 0.325 | 0.181 | 0.447 | 0.236 | **0.242** | 1.34× | 0.54× |

(ms, Execution Time. B03's three runs: 68.98 contended, 98.83 with 33 pages read, 77.36.)

## STOP-P: hit on read 04, by the letter

d091691's STOP-P: no whole-table scan that was not one, and no read more than
3× its d091691 timing.

- **Scans.** No plan scans `cpc_outlook_curves`. The whole-table scans are of
  `cpc_curve_verdicts` (1,440 rows, 120 pages): the `nv` lateral, and
  PLACES_SQL's `hashes` and `lv`. B scans it the same number of times, read
  for read. d091691's plans scanned it once more in each read: in CURVES_SQL
  the view's `newest` CTE was a Seq Scan at 720 rows and is an Index Only Scan
  on `idx_ccv_version` at 1,440; in PLACES_SQL the cells were a Seq Scan and
  are a Bitmap Heap Scan on `idx_ccv_version`. Counts per read, A / B /
  d091691: 1/1/2 in each CURVES_SQL, 2/2/3 in PLACES_SQL, 0/0/0 in
  PLACES_NEWEST_SQL. No scan is new.
- **Timing.** Reads 01, 02, 03, 05 and 06 are within 3× of d091691 (1.02×–2.57×).
  **Read 04 is 18×: 24.0 ms against 1.33 ms.** It is not the same read:
  - under v1, KDEN (24 base years) had no window row, so `w` was NULL on all
    14 issuances, the One-Time Filter `w.method_version = 'cpc_curves_v1'` was
    false, and the view lateral was `(never executed)`;
  - under v2, KDEN (29 base years, threaded) is written and drawable, so the
    view lateral runs for each of the 14 issuances and returns 84 rows.
  
  The same request now draws 14 frames where it drew none. The cost is the
  cost of any drawn place at n=14 (read 03, pnw, 112 rows: 31.3 ms).

**Where the time goes, in every drawn read.** The view's `newest` CTE
(`DISTINCT ON (method_hash) … ORDER BY method_hash, scored_at DESC,
backtest_version DESC`) is re-run once per view row: Unique over an
Incremental Sort over an Index Only Scan of `idx_ccv_version`.
- Under v2 the scan reads 721 entries a loop, the v2 hash `b201e09ff97d` then
  one more, because it sorts first: about 0.25 ms a loop. That is 84 loops in
  read 04 (21 ms of 24) and 112 in read 03 (28 ms of 31).
- Under v1 today (B), the v1 hash `e99c71234013` sorts second. Every loop reads
  all 1,440 entries and sorts two groups, about 0.58 ms a loop. That is why
  production's read 03 is now 77 ms, against d091691's 12.2 ms the day before
  v2's verdicts were written.

This is d091679 §8.2, still open in pantry: a view that computes the version
in force once per statement, or a status view (§8.1), would make each loop an
index probe. It is pantry's view; this lane changes no SQL beyond the pin and
the count columns.

**Against production today**, every v2 read but 04 is faster or level
(A/B 0.27×–1.00×). Read 04 is 4.15× production's because production draws
nothing there.

## What the pin does in each plan (A against B)

| site | B (v1 pin) | A (v2 pin) |
|---|---|---|
| CURVES_SQL `iss` | Index Only Scan Backward `uniq_coc_row`, Index Cond (product, method_version = v1): 1,769 / 1,327 rows returned at n=14; heap fetches 22 on 814temp | the same index, method_version = v2: 3,121 / 2,341 rows returned at n=14 (v2 writes 30 places per issuance against v1's 17), 0 heap fetches |
| CURVES_SQL `w` | `uniq_coc_row`, full key, v1 | the same, v2 |
| CURVES_SQL `nv` | Seq Scan `cpc_curve_verdicts`, Filter hash and v1, 720 removed | the same, v2, 720 removed (Memoize at n=14, 13 hits) |
| CURVES_SQL `vv` | `uniq_ccv_cell`, Filter v1 and hash | the same, v2 |
| CURVES_SQL `dv` | One-Time Filter v1; per row `uniq_coc_row`, then the view's `newest` (1,440 entries a loop) | One-Time Filter v2; the same, 721 entries a loop |
| PLACES_SQL | HashAggregate over a Seq Scan (v1), LIMIT 1 per hash over a Seq Scan, Bitmap Heap Scan on `idx_ccv_version` for the cells | the same three, v2 |
| PLACES_NEWEST_SQL | Index Only Scan Backward `uniq_coc_row` per product (v1), then `uniq_coc_row` (product, issued_date, day_index, v1): 34 rows | the same, v2: 60 rows |

**The `iss` walk** (d091691's named risk, now from the v2 side).
`method_version` is the last column of `uniq_coc_row` (product, issued_date,
place_kind, place, weighting, day_index, method_version), so the backward walk
within the product passes every v1 entry of each day both versions wrote and
drops it in the index, with no heap visit. The rows column counts only the v2
entries returned: 3,121 at n=14 on 814temp, in 0.7 ms. The v1 entries it
passes: 136 a day on 814temp and 102 on 610temp, for the days v1 also wrote
(none on 10-10, the newest). Bounded by days, not by the table, as d091691 said.
It ends when v1 is retired (the captain's decision, not this lane's).
