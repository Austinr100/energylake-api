# Handback d091757: held — the rows are in Neon now, but d091746 has not merged, so `/outlook` and `toa` were not built

**Lane:** d091757 · **Repo:** energylake-api · **Branch:** `claude/tropics-outlook-toa-api-99d0fq`, from main at `a7af1b0`.
**Ran:** 2026-10-15, about 21:35Z–22:00Z. The Neon reads are stamped from 21:40:05.926Z. **Environment:** EL 6.
**Rulings checked:** D-09-25-231 (the dependency on d091746) · D-09-05-T (the vectors are banked rows, never constructed) · D-09-25-75 · D-09-25-76.
**Second run.** The first run (`claude/kind-pascal-b75uho`, `ff8a21e`, 18:44Z) stopped on both dependencies. Since then one of them has cleared: d091756's backfill has written its rows.

**Scope kept:**
- Branch only. No PR, no merge, no deploy.
- **No Neon write.** Every read went through the Neon connector inside a transaction that opened with `SET TRANSACTION READ ONLY` and ran SELECT or `EXPLAIN (ANALYZE, BUFFERS) SELECT` only. One transaction failed on a column I guessed wrong (`created_at`) and read nothing.
- **No R2 read.**
- **Network:** GitHub (this repo's PR list, read only) and Neon (read).
- No route, test or fixture was built, and no vector was banked.

---

## 0. The hold

### STOP-D now passes

```sql
SELECT count(*) FROM tropical_file_vintage
 WHERE source='display' AND product IN ('display:two_areas','display:toa_earliest');
-- stop_d = 91   (2026-10-15 21:40:05.926Z, project fancy-block-96153928, default branch)
```

At 18:44Z this returned 0. All the outlook and arrival-time rows carry `fetch_ts = 2026-10-15 21:17:51.515087Z`, so d091756's backfill ran at 21:17Z. All are `status = 'banked'`.

| product | rows |
| --- | --- |
| display:two_areas | 35 |
| display:two_points | 35 |
| display:two_text_at | 26 |
| display:two_text_ep | 26 |
| display:toa_earliest | 56 |
| display:toa_mostlikely | 56 |

### d091746 has still not merged, so the lane holds

- origin/main is still `a7af1b0`, the commit this lane branched from. Its `tropics_index.py` has no `DISPLAY_FILES_SQL` and reads no `tropical_display_in_force`. The display read is still `FILES_SQL` on `tropical_file_vintage_in_force` by `vintage_key`.
- `docs/handback_2026_10_15_tropics_api_revision_read_d091746.md` does not exist.
- This repo's PR list (newest updated first) shows no d091746 PR. The last merge was #116 at 15:56Z.
- No branch on origin reads `tropical_display_in_force` in code. The name appears only in the `REVISION_READ_XFAIL` reason in `tests/test_tropics_index_d091741.py:593`.

**Why this alone stops the lane.** Item 2 says to add the two kinds to `ADVISORY_PRODUCTS`' served names and read them with "the same `DISPLAY_FILES_SQL`", and D-09-25-231 puts that read on `tropical_display_in_force` by `advisory_or_cycle`. On main there are two ways to proceed, and both are wrong:

1. **Read TOA through main's `FILES_SQL`.** That breaks D-09-25-231 and serves the wrong object for corrected advisories. The TOA bank already holds four corrections: `ep182026` has 3 `r<n>` rows per kind and `ep212026` has 1. Main's read asks `vintage_key = '<ADV>'` and would serve each of them its revision 0. That is the defect the I2 xfail names.
2. **Write `DISPLAY_FILES_SQL` here.** That builds d091746's lane inside this one. When d091746 lands there would be two versions of the same read, a conflict in `tropics_index.py`, and two sets of revision tests.

The whole lane holds, `/outlook` included. Section 3 explains why I didn't split it.

## 1. What fires this lane again

1. **d091746 merges to main.** That is the only open condition. The rows exist, and STOP-D will pass as written.
2. Re-fire the paste, with the corrections in §3 folded in. The brief's body shape and its `stamp` parameter are both wrong for the rows as banked.

## 2. What the rows show (recon for the re-fire; read only, nothing banked)

### The outlook rows

- **The TWO rows have `storm_id = ''`, not NULL.** Each is keyed by a 12-digit `vintage_key` (`YYYYMMDDHHMM`), and the view's `advisory_or_cycle` pattern `[0-9]{12}` takes the whole key. No TWO row has a revision suffix.
- **The areas, points and text rows of one issuance have different stamps.** Newest issuance:

  | product | vintage_key / stamp | issued_ts | meta |
  | --- | --- | --- | --- |
  | two_areas | 202610151751 | 17:51Z | n_features 1 |
  | two_points | 202610151751 | 17:51Z | n_features 0 (107 bytes) |
  | two_text_at | 202610151751 | **18:00Z** | 2 paragraphs, Beven, `disturbances: []` |
  | two_text_ep | **202610151730** | **18:00Z** | 3 paragraphs, Papin, 1 disturbance |

  The 12Z issuance repeats the pattern: areas `…1138` at 11:38Z, text_at `…1135`, text_ep `…1136`, both texts at 12:00Z. Each text's `issued_ts` is the synoptic hour; the areas' `issued_ts` is the actual minute. **Only the areas/points pair shares a stamp.** A join on `stamp` would find no text for `ep` at 18Z, and none for either basin at 12Z.
- **There is no `display:two_text_cp`.** `two_text_ep`'s heading (`meta.basin`) reads *"For the eastern and central North Pacific east of 180 longitude:"*, so `cp` is served `ep`'s text. Per item 1, the body should state that rather than imply a separate CPHC product. `meta.basin` holds that heading line, not a basin code.
- **The text meta has no `paragraphs` array of the brief's shape.** It carries `n_paragraphs` and `disturbances: [{title, number, prob_2day, prob_7day, risk_2day, risk_7day}]`, plus `forecaster`, `awips_id`, `wmo_heading`, `bbb`, `issued_local`, `issued_utc`, `transmitted_utc`, `title` and `formation_not_expected`. `number` is null for the one disturbance banked, and there is no `headline` field. Under D-09-25-76, `area_num` and `headline` cannot be derived from `title` or the paragraph text.
- **Spacing:** 35 areas issuances between 2026-10-09 11:43Z and 2026-10-15 17:51Z (about 6.25 days). The routine 6-hourly cycle alone would give about 25, so specials are in the bank. The 12 h `not_banked` threshold is about two missed cycles. At the read, the newest TWO was 3.8 h old.

### The arrival-time rows

| storm | rows per kind | advisories | corrected (r≥1) | newest issued |
| --- | --- | --- | --- | --- |
| al092026 | 5 | 011–015 | 0 | 2026-10-10 09Z |
| ep182026 | 13 | 049–058 | 3 | 2026-10-11 15Z |
| ep202026 | 27 | 008–034 | 0 | 2026-10-15 21Z |
| ep212026 | 11 | 001–010 | 1 | 2026-10-15 21Z |

- `r2_key` is `tropics/<storm>/adv<NNN>/toa_<kind>.geojson`.
- The TOA meta carries `n_features`, `vertices`, `labels`, `labels_null`, `advisory_in_object`, `advisory_check` and the `source_*` provenance, the same family as the cone's.
- **The `not_issued` refs exist.** The storm entries' `meta.refs` include `toa_earliest_kmz` and `toa_mostlikely_kmz`, so the cone's rule (`cone_kmz` in refs) carries over as `(served name, ref key)` pairs:

  ```python
  ("toa_earliest", "toa_earliest_kmz"), ("toa_mostlikely", "toa_mostlikely_kmz")
  ```

### A candidate outlook read, and its plan (X1 preview, not the deliverable)

Following CLAUDE.md, the read takes one `(product, storm_id='')` per lateral and the newest by `advisory_or_cycle`:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT vintage_key, issued_ts, r2_key, sha256, byte_size, meta
  FROM tropical_display_in_force
 WHERE product = 'display:two_areas' AND storm_id = ''
 ORDER BY advisory_or_cycle DESC LIMIT 1;
```

```
Limit  (actual time=0.252..0.253 rows=1)  Buffers: shared hit=13
  ->  Sort  Sort Key: advisory_or_cycle DESC  (top-N heapsort)
        ->  Subquery Scan on tropical_display_in_force  (rows=35)
              ->  Unique  (rows=35)
                    ->  Sort  (quicksort 63kB)
                          ->  Index Scan using tfv_identity on tropical_file_vintage v  (rows=35)
                                Index Cond: ((source = 'display') AND (product = 'display:two_areas') AND (storm_id = ''))
                                Filter: (status = 'banked')
Planning Time: 0.151 ms   Execution Time: 0.276 ms
```

- **The `product` and `storm_id` predicates push through the view's `DISTINCT ON`, so this is an index scan on `tfv_identity` with no seq scan.** The DISTINCT ON keys lead with `(product, storm_id, …)`.
- **The read sorts every banked issuance of the product: 35 now, and growing by about 4 a day.** That is cheap today. It grows without bound, as `DISTINCT ON` does in CLAUDE.md's d091551 note.
- The text rows need the same read with `product = 'display:two_text_<basin>'`. Each text row has to be paired with an areas row by issuance, not by stamp (see §3).

## 3. What this brief got wrong

1. **It was fired, twice, before d091746 merged.** The header says "fire after d091746 merges", but STOP-D gates only the rows. A gate on main carrying `DISPLAY_FILES_SQL` (or d091746's handback) would have held both dispatches without a run.
2. **`stamp` names an issuance, and the bank has no single stamp per issuance.** Areas and points share `YYYYMMDDHHMM`; the two texts each have their own (`…1751` / `…1730` at 18Z). Before a re-fire, the architect has to choose:
   - Key `?stamp=` to the areas' stamp, and pair the text whose `issued_ts` is the next synoptic hour at or after the areas' `issued_ts` (17:51Z → 18:00Z); or
   - Key `?stamp=` to the synoptic hour (`issued_ts` of the text) and serve the areas issued in the preceding window.

   Either way, the body should print each object's own stamp rather than one shared `stamp`.
3. **`paragraphs: [{area_num, two_day_pct, seven_day_pct, headline}]` is not in the bank.** The bank holds `disturbances: [{title, number, prob_2day, prob_7day, risk_2day, risk_7day}]` and `n_paragraphs`. Serving it means renaming fields and leaving `headline` out, or passing `disturbances` through verbatim (the safer reading of D-09-25-76). Either way, `area_num` is `number` and is null when NHC gives no number.
4. **`cp` is not a guess any more.** The bank holds no CPHC text, and `two_text_ep` states it covers "the eastern and central North Pacific east of 180". The rule "`cp` shares `ep`'s text" is right, and `basin_rule` can quote that heading.
5. **I did not split the lane.** `/outlook` alone does not need d091746's code: its read goes straight to the view, as in §2. But the brief fires one lane on one dependency, with a mutation rehearsal (R) across both routes. Building half would leave U2 and half of R for another dispatch, and would open `tropics_index.py` beside d091746's pending change to the same module. If the architect would rather have `/outlook` now, it can be its own brief once §3.2 and §3.3 are ruled. It needs nothing from d091746.

## 4. The two bodies, the banking stamp, the suite counts

None. The bodies and the banking stamp need the build, and nothing on this branch changes code, so the suite counts are main's. The suite was not run.
