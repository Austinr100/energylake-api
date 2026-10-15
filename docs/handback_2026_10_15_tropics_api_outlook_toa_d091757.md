# Handback d091757: STOP-D — Neon holds no outlook or arrival-time rows, so `/outlook` and `toa` were not built

**Lane:** d091757 · **Repo:** energylake-api · **Branch:** `claude/kind-pascal-b75uho`, from main at `a7af1b0`.
**Ran:** 2026-10-15, about 18:40Z–18:50Z (container clock; the Neon read is stamped 18:43:57.803Z). **Environment:** EL 6.
**Rulings checked:** D-09-25-231 (the dependency on d091746) · D-09-05-T (vectors are banked rows, never constructed).

**Scope kept:**
- Branch only. No PR, no merge, no deploy.
- **No Neon write.** There were two read transactions through the Neon connector. Each opened with `SET TRANSACTION READ ONLY` and ran SELECTs only. The first failed on a column name I guessed wrong (`fetched_ts`) and read nothing.
- **No R2 read.**
- **Network:** GitHub (this repo's PR list, read only) and Neon (read).

---

## 0. STOP-D

The brief's stop condition, run as written:

```sql
SELECT count(*) FROM tropical_file_vintage
 WHERE source='display' AND product IN ('display:two_areas','display:toa_earliest');
-- stop_d = 0   (2026-10-15 18:43:57Z, project fancy-block-96153928, default branch)
```

**It returned 0, so the lane stopped here.** Nothing was built and no test was written. Banking vectors from rows that don't exist would break D-09-05-T.

What Neon does hold under `source='display'`, by product, at the same read:

| product | rows |
| --- | --- |
| display:cone | 88 |
| display:tcp | 88 |
| display:ww | 70 |
| display:tcd | 56 |
| display:wsp34_bands / _grid | 26 / 26 |
| display:wsp50_bands / _grid | 26 / 26 |
| display:wsp64_bands / _grid | 26 / 26 |
| display:surge_ww | 5 |

There is no `display:two_*`, no `display:toa_*`, and no other outlook product. **d091756's backfill has not run against this database**, or it wrote under other product names. Its handback (pantry `docs/handback_2026_10_15_publish_outlook_toa_d091756.md`) was not read in this lane. The view `tropical_display_in_force` does exist (`to_regclass` resolves it).

### The second dependency is also unmet: d091746 has not merged

- main's `tropics_index.py` (at `a7af1b0`) still has no read through `tropical_display_in_force` by `advisory_or_cycle`.
- `docs/handback_2026_10_15_tropics_api_revision_read_d091746.md` does not exist.
- A search of this repo's PRs for `d091746` finds nothing.

So the lane fails both of the brief's "Depends on" conditions, and either one alone was reason to hold it.

## 1. What fires this lane again

1. d091746 merges to main. `DISPLAY_FILES_SQL` then reads `tropical_display_in_force` by `advisory_or_cycle`.
2. d091756's backfill writes its rows. The same STOP-D count then returns more than 0.
3. Re-fire the brief's paste as written. Nothing in it needs to change for these two holds.

## 2. The two bodies, the EXPLAIN, the banking stamp

None. Each needs rows that don't exist yet.

## 3. What this brief got wrong

- **It was fired before either of its dependencies held.** Its own header says "fire after d091746 merges", and the STOP-D gate caught the problem as designed. A pre-fire check of the same `count(*)`, plus a look at main for d091746's handback, would have saved the dispatch.
- Nothing else can be judged until the rows exist. That includes whether `cp` has its own TWO text in the bank, and the 12 h `not_banked` threshold against NHC's real issuance spacing.
