# Handback — d091693, the CPC API flip to cpc_curves_v2: STOPPED at STOP-0

**Lane:** d091693 · **Repo:** `energylake-api` · **Branch:** `claude/cpc-api-flip-v2-dcaf8g`
**Outcome:** STOP-0 failed on both conditions. Nothing was flipped: `cpc_outlooks.py`, `main.py`, the tests and the vectors are unchanged. This file is the only change on the branch. Nothing was written to Neon or R2.

## STOP-0's counts (V0)

Read from Neon project `fancy-block-96153928`, default branch, on 2026-10-10 at 20:55:17Z:

| check | v1 | v2 | needed | holds? |
|---|---|---|---|---|
| distinct `(product, issued_date)` in `cpc_outlook_curves` | 3599 | **321** | v2 = v1 | **no** (3278 short) |
| rows in `cpc_outlook_curves` | 428264 | 61980 | — | — |
| `cpc_curve_verdicts`, cells per `backtest_version` | `cpcv_2026-10-03_d0a93d5811`: 720 | **none** | one v2 version of 720 | **no** |

By product, read at 20:55:09Z:

| product | v1 issuances | v2 issuances |
|---|---|---|
| `610temp` | 1800 | 251 |
| `814temp` | 1799 | 69 |

The v2 issuances run from 2026-01-23 to 2026-10-09. v1 runs from 2021-10-26 to 2026-10-09.

The backfill is still writing. Three reads about 10 s apart gave 319, then 320 (by product), then 321 v2 issuances. The rows are arriving, but the backfill is about 9% done, and no v2 verdicts have been written.

Queries (read only):

```sql
SELECT method_version, count(*) AS rows,
       count(DISTINCT (product, issued_date)) AS issuances,
       min(issued_date), max(issued_date)
FROM cpc_outlook_curves GROUP BY method_version ORDER BY 1;

SELECT method_version, backtest_version, count(*) AS cells
FROM cpc_curve_verdicts GROUP BY 1, 2 ORDER BY 1, 2;
```

## What changed

Nothing in code. Production still serves `cpc_curves_v1` only, as pinned by d091691 (#113), so the v2 rows being written beside v1 do not reach any body. That is the P-tests' guarantee working as intended.

## Body differences, vectors, plans

None were taken. The lane stopped before any of them, as the brief requires.

## What this brief got wrong

- The brief was fired before its own gate. "Fire it only after the v2 backfill is complete and the first v2 verdicts are written" did not hold at 20:55Z, two hours after the 18:47Z sample. STOP-0 caught it, which is what it is for.
- No other findings: the lane did not get far enough to test the rest of the brief against the code.

## To re-fire

Re-fire the same paste unchanged once both of these hold:

1. `count(DISTINCT (product, issued_date)) WHERE method_version = 'cpc_curves_v2'` equals v1's count. v1's count was 3599 at this reading; it grows by one per product with each new issuance.
2. `cpc_curve_verdicts` holds one `cpc_curves_v2` `backtest_version` with 720 cells.
