# Handoff — The Regime Package v0 (`energylake-api`)

**Date:** 2026-07-30
**Concept:** `energylake-dashboard/docs/concept_capture_2026_07_30_regime_package.md`
**Branch:** `claude/regime-api-v0`, branched off `main` at `9a66c2c` (after the
fuel micro-lane, PR #53, merged — same file, conflict avoided)
**House law followed:** `docs/handoff_2026_07_30_analytics_department_v0_api.md`
(the Nodes lane) — its chunk shapes, percentile convention and shape-guard
pattern are used verbatim, not re-invented.

**Scope shipped:** all five pieces + 144 new tests.
Suite: **858 passed** (144 new, 714 pre-existing, **none modified**).
**No migrations. No existing endpoint changed** — the two additive fields on the
node package are the captain's explicit requirement (piece 2), and the package's
top-level key set is unchanged and still pinned by the Nodes lane's own test.

---

## 1. What shipped

| # | endpoint | new query shapes |
|---|---|---|
| 1 | `GET /api/analytics/node/{pnode_id}/ladder?metric=dart\|basis` | none |
| 2 | basis classing + stance folded into `GET /api/analytics/node/{pnode_id}` | none |
| 3 | `GET /api/analytics/node/{pnode_id}/grid?metric=dart\|basis` | none |
| 4 | `GET /api/analytics/tb4?scope=hub\|node&id=&window=` | 1 (hub full depth) |
| 5 | `GET /api/analytics/movers-grid?window=1d\|7d&metric=dart\|basis` | 1 (per-node per-HE) |

Pieces 1, 2, 3 and node-scope TB4 introduce **zero** new query shapes. They ride
`_AN_NODE_LEG_SQL` / `_AN_HUB_LEG_SQL` / `_AN_DEPTH_SQL` — the per-node
index-only reads the Nodes lane already measured — and do their work in Python
over ~192 DAM / ~700 RTPD rows. Only two shapes are new, both EXPLAIN'd (§5).

---

## 2. Depth report, measured 2026-07-30

`atlas_pnode_lmp_snapshot`, sentinel-floored:

| market | min_date | max_date | dates | complete | rows |
|---|---|---|---|---|---|
| DAM  | 2026-07-23 | 2026-07-30 | 8 | 6 (07-24…07-29) | 2,490,936 |
| RTPD | 2026-07-23 | 2026-07-30 | 8 | 6 | 9,741,339 |

The window **rolled during this lane's build** (the Nodes lane measured
07-22…07-29). Both edges are partial: 07-23 opens late, 07-30 runs to HE16. The
tier is still ~7-day retention with a dispatch-only writer and no standing
schedule. **Every per-HE ladder row is therefore n = 6…8 today.**

`timeseries_values` / `caiso_lmp_da_hourly`, per hub zone:

| series | rows | span | TB4 days |
|---|---|---|---|
| NP15 / SP15 / ZP26 | 88,344 each | 2016-01-01 → 2026-07-31 | **3,682** |

That is **122 months**, not the 118 the capture estimated — the measured figure
is what ships in the payload.

**Read this before wiring a "latest" caption:** the hub leg is a **day-ahead**
series, so its newest TB4 day is **tomorrow** (2026-07-31 as of this writing —
24 hours, fully published). That is correct for DAM, not a clock bug.

---

## 3. The depth grammar — and the one interpretation I had to make

The floor is served, not captioned. Below `AN_PERCENTILE_FLOOR = 15` a ladder
row carries `min`/`mid`/`max`, `vocabulary: "range"`, and **no percentile key
and no `chip` key exist on it** — absent, never nulled, because
`row.p95 ?? fallback` renders the fallback while `"p95" in row` cannot be
misread. 15 is the dashboard's own `PERCENTILE_FLOOR`; a test pins them equal.

**The call I made, stated plainly.** "Basis classing … same depth grammar" could
be read as *withhold the class below the floor*. I did not read it that way, for
two reasons, and this is the one place a captain might want to overrule me:

1. Reading it that way makes piece 2 **impossible to deliver**. At n = 6…8 there
   would be no class, `computeBasisStance` would keep returning null, and the
   "lights with zero client edits" requirement would fail at exactly today's
   depth.
2. The dashboard's own `stance.ts` says it explicitly: *"DEPTH GRAMMAR STILL
   STANDS. The classes' own words ('upper', 'lower') are the vocabulary. This
   module never prints p5/p25/p50/p75/p95 at any depth."* The class names are
   internal labels; the depth grammar governs the **words a surface prints**.

So: **`class` is served at every depth** (as `band` on dart latest-day rows
always has been), and **`context` is the depth-aware string** — "upper range"
below the floor, "p75–p95" at or above it. That is `bandLabel(band, n)` from
`src/components/analytics/analytics.ts`, mirrored verbatim and pinned by a
parametrized test over all six classes at both depths. The **ladder's** floor —
percentile columns and chip — is the strict absent-key rule the spec demanded.

---

## 4. Piece 2 — how the dashboard's gate actually opens

`computeBasisStance` reads `rec.band ?? rec.class` and pushes it through
`classifyBand`, which accepts exactly six names. So the requirement was precise:
`basis.per_he[]` rows now carry

```json
{"he": 1, "block": "offpeak", "node": 58.5901, "hub": 56.342, "basis": 2.2481,
 "n_hours": 7, "n_days": 7,
 "current": 2.2932, "current_date": "2026-07-30",
 "class": "p25_p50", "context": "below median", "class_n": 7}
```

— live payload, `ALAMT3G_7_B1`, read 2026-07-30. `rec.class` hits the branch,
the detector lights, **zero client edits**.

**What gets classed is the LATEST banked day's basis at that hour**, against that
hour's own banked basis history — the same reading the dart rows carry. Classing
the row's window *mean* against its own window would cluster every hour at the
middle and mean nothing. `current_date` travels with it because the frontier is
ragged (mid-dispatch today holds HE1…HE16, so HE20's latest day is yesterday).

**Stance placement — a deliberate deviation.** The captain said "the package
payload gains stance objects for BOTH metrics." I put them at
`dart.stance` and `basis.stance`, **not** top-level, because
`test_package_has_all_five_stanzas_plus_honesty_fields` asserts an **exact**
top-level key set. A top-level `stance` key would have broken a pinned existing
test, and "nothing existing touched" outranks key placement. Nesting also reads
better: each verdict lives with the metric it counts.

Live, same node:

```
dart.stance : BEARISH — "0h upper / 4h lower of 19 banked → real time ran
              below its banked shape in more hours than above"
basis.stance: BULLISH — "16h upper / 4h lower of 24 banked → real time ran
              above its banked shape in more hours than below"
```

The `line` string mirrors `stance.ts` **byte for byte** (same counts, same
arrow, same reads) so the two computations of one truth agree exactly.

> **One wart, reproduced rather than fixed.** `stance.ts`'s `readFor()` is
> metric-agnostic and DART-worded, so the **basis** verdict reads "real time ran
> above its banked shape" — which is not what a basis measures. I mirrored it
> anyway: a server line that read better but differently would be a *second*
> truth, which is the exact thing this piece exists to abolish. **Rewording is a
> dashboard-lane change** (`readFor` needs a metric argument); when it lands,
> `AN_STANCE_READS` in `main.py` must move with it, and the byte-identity test
> will catch it if it doesn't.

---

## 5. EXPLAIN ANALYZE receipts — two new shapes, one rejected alternative

All on the live pantry, 2026-07-30, Neon 0.25–2 CU / pg17.

### `_AN_TB4_HUB_SQL` — hub TB4 over full depth

```
Index Scan idx_tsv_series_ts (88,344 rows)
  -> Sort (external merge, 1,968 kB)
  -> Aggregate, Sorted, array_agg(value ORDER BY value)  -> 3,681 daily rows
Execution Time: 148.083 ms   (all buffers hit)
Execution Time: 188.821 ms   (1,780 blocks read cold)
```

**The planner did misbehave on the obvious shape, and I report the plan.** The
natural way to write "top 4 / bottom 4 per day" is
`row_number() OVER (PARTITION BY day ORDER BY value DESC/ASC)` plus
`count(*) OVER (…)`:

```
3 x WindowAgg + Incremental Sort x2 + Sort (external merge, 2,944 kB)
all 88,344 rows materialised through every window frame
Execution Time: 1,868.378 ms
```

**12.6× slower for an identical answer**, and a 50% larger spill. `array_agg` +
array slice needs **one** sort; the per-group sort is 24 elements wide and
effectively free. The array_agg shape is pinned, and
`test_tb4_hub_sql_slices_the_measured_shape_not_a_window_frame` fails if anyone
reintroduces `row_number()`.

I did **not** chunk it: one index-served read of a 122-month series at 150–190 ms
is already inside budget, and chunking by year would multiply round trips
without changing the sort.

### `_AN_NODE_HE_SQL` — the movers-grid fill, 50 nodes

`pnode_id` leads the snapshot PK, so `ANY(array)` is index-served:

```
RTPD, 50 nodes x 7 dates:
  Bitmap Index Scan atlas_pnode_lmp_snapshot_pkey (7.9 ms)
  -> Bitmap Heap Scan 31,350 rows
  -> HashAggregate, Batches: 1, Peak Memory 2,321 kB, Disk Usage 0
  Execution Time: 236.609 ms  (cold: 1,477 blocks read)

DAM, 50 nodes x 7 dates:
  same plan, 8,000 rows, HashAggregate 1,937 kB, Disk 0
  Execution Time: 57.033 ms
```

No sort, no spill, both markets, one shape. **Ranking the universe per HE
instead** is the shape the Nodes lane already measured and refused:
`GROUP BY (pnode_id, market_date, market_hour)` over a 7-day RTPD window
seq-scans 9.5M rows and **spills 430 MB for 38 s**. Rank first, fill 50 nodes
second — three orders of magnitude cheaper.

---

## 6. END-TO-END WALL CLOCK — the caveat the Nodes lane could not clear

The Nodes lane could not open a pool against Neon and said so. **I could.**
Every endpoint below was driven in-process against the live pantry, 2026-07-30,
from a Windows host over the public internet to `aws-us-east-2` (so these are
*pessimistic* relative to Railway, which sits far closer):

| request | cold cache | cached |
|---|---|---|
| `ladder?metric=dart` | 731 ms – 1,301 ms | n/a |
| `ladder?metric=basis` | 770 ms – 828 ms | n/a |
| `grid?metric=dart` / `basis` | 631 – 710 ms | n/a |
| node package (with classing + stance) | 818 – 950 ms | n/a |
| `tb4?scope=hub` (full 3,682-day depth) | 327 / 429 / 903 ms per zone | 0 ms |
| `tb4?scope=node` | 573 – 723 ms | 0 ms |
| `movers-grid?window=1d&metric=dart` | **3,218 – 22,143 ms** | 0 ms |
| `movers-grid?window=7d&metric=dart` | **11,856 – 30,901 ms** | 0 ms |
| `movers-grid?window=1d&metric=basis` | 3,068 – 13,181 ms | 0 ms |

**The phase split, measured directly** (run the grid, clear only the grid cache,
run again so phase 1 comes from the movers cache):

| window | rank + fill | fill only | rank share |
|---|---|---|---|
| 1d dart | 3,218 ms | **416 ms** | 2,802 ms (87%) |
| 7d dart | 11,856 ms | **835 ms** | 11,021 ms (93%) |

**Read that honestly, because it revises a number the department is carrying.**
The Nodes lane estimated `dart` 1d at ~2.0 s cold and 7d at ~12 s cold by
dividing summed server-side `EXPLAIN` times by the concurrency factor. The real
wall clock is **3.2–22 s for 1d** and **12–31 s for 7d**. The spread is the
0.25–2 CU compute genuinely swinging under autoscale, and the estimate was
optimistic at the low end and badly optimistic at the high end.

The grid itself is **not** the problem: it adds 0.4–0.8 s. **The cold path is
the movers cold path**, which is the case for the precompute follow-on, below.

---

## 7. Hand-derivation acceptances (all four, pinned)

**(a) One full ladder row, every percentile by hand.** The pantry holds eight
dates, so it **cannot** supply a row at or above the floor today — this
acceptance therefore rides a **constructed** 21-day series and says so in the
test docstring. Samples `v_i = 10i`, `i = 0…20`, so `n-1 = 20` and
`percentile_cont(q)` reads index `20q`; uniform spacing makes the value at index
`x` exactly `10x`:

```
P01 -> idx 0.2  ->   2     P25 -> idx  5 ->  50     P90 -> idx 18   -> 180
P05 -> idx 1    ->  10     P50 -> idx 10 -> 100     P95 -> idx 19   -> 190
P10 -> idx 2    ->  20     P75 -> idx 15 -> 150     P99 -> idx 19.8 -> 198
```

`test_acceptance_full_ladder_row_derived_by_hand`.

**(b) One regime chip rank, by hand.** Same series, current = 190, which sits at
index 19 and appears once: `rank = 19/20 = 0.95 -> P95 -> 95 >= 75 -> HIGH
(P95)`. The round trip holds — `percentile_cont(0.95)` reads index 19 = 190.
`test_acceptance_regime_chip_rank_derived_by_hand`.

**(c) The real pantry rows, at the depth actually banked.**
`ALAMT3G_7_B1` HE20 DART, seven banked days, sorted:

```
-35.94077, -26.4196525, -16.257845, -3.4686275, 15.1101575, 19.3123367, 1019.095925
min = -35.9408   mid = -3.4686 (4th of 7)   max = 1019.0959
n = 7 < 15  ->  vocabulary "range", no percentile key, no chip
current = -35.9408 (2026-07-29)
```

The **live endpoint returns exactly this**, and the heat grid's HE20 `row_avg`
comes out **138.7759** — which is the Nodes lane's own independently measured
`HE20 mean 138.776`. Two lanes, two code paths, same number.
`test_acceptance_real_pantry_he20_is_a_range_row_with_no_chip`.

**(d) One TB4 day, hours named.** SP15 day-ahead, 2026-07-29, 24 hours banked:

```
top 4    HE20 86.105330 | HE21 75.525770 | HE22 72.321160 | HE19 65.475550
         (HE23's 64.032250 is next, excluded)   sum 299.427810 -> 74.85695250
bottom 4 HE11 37.143810 | HE10 37.308310 | HE12 37.356590 | HE9  37.372350
         (HE13's 37.466270 is next, excluded)   sum 149.181060 -> 37.29526500
TB4 = 74.85695250 - 37.29526500 = 37.56168750  ->  37.5617 on the wire
```

The evening peak against the solar midday belly, which is the whole point of the
metric. `test_acceptance_tb4_day_named_hours_and_arithmetic`.

**(e) One movers-grid cell** (bonus, same discipline): node AAA, HE5, dates
07-29/07-30, DART +6.0 and +1.0 -> cell 3.5, `cell_n` 2; HE6 banked one day ->
12.0, `cell_n` 1; HE7 banked neither -> `null`, `cell_n` 0.
`test_acceptance_movers_grid_cell_derived_by_hand`.

---

## 8. Two defects the work surfaced, and what I did about them

**(a) The percentile rank clamped a tie run to P0.** My first
`_an_percentile_rank` clamped with `v <= s[0]`, so an all-equal sample reported
its own value as **P0 — "LOW"** — which is the single wrong answer that function
must never give. The test caught it before the suite was green. Clamps are now
strict (`v < s[0]`), so an extreme falls through to the tie-run rule and an
all-equal hour ranks P50 / MID. Pinned by
`test_percentile_rank_of_a_tie_run_is_its_midpoint`.

**(b) `regime_shift` produced a confident false at nodal depth.** Driving the
live endpoint showed `scope=node` returning `flag: false, delta: 0.0000`. With
seven banked days **both trailing windows resolve to the same seven days**, so
the delta is *necessarily* zero and the flag *necessarily* false. That is not
"no regime shift" — it is "no comparison" — and `flag: false` reads as a
finding. The verdict is now **withheld** with its reason:

```json
{"available": false,
 "reason": "both windows resolve to the same 7 banked days, so the comparison
            is not informative at this depth",
 "threshold_definition": "|avg_7d − avg_30d| > 0.25 × |avg_30d|",
 "threshold_fraction": 0.25}
```

The threshold still ships at every depth so the rule stays visible. Hub scope is
unaffected (3,682 days; SP15 currently flags **true**: `|43.7509 − 29.8011| =
13.9498 vs 0.25 × |29.8011| = 7.4503 → regime shift`).

---

## 9. The threshold is a proposal, not a decision

`AN_TB4_SHIFT_FRACTION = 0.25` is the capture's suggested 25%. It is a single
module constant, it is **serialized in every payload** as
`threshold_fraction` + `threshold_definition` + the full `arithmetic` string, so
the flag is auditable without reading `main.py` and re-tuning it is a one-line
change. All three hub zones flag `true` at 0.25 right now — worth a captain's
eye on whether that is the market talking or the fraction being loose.

---

## 10. FOLLOW-ON LANE (named, and explicitly NOT in this PR)

**A precomputed movers artifact, written pantry-side.** Cron + catalog entry at
birth, per the concept capture's own §2. §6 above is the evidence: 87–93% of the
movers-grid cold path is the ranking, and no query shape fixes it — the Nodes
lane measured every wider shape and they are all worse. The endpoint computes
live behind a 5-minute cache and **says so in the payload**:

```json
"precompute": {"built": false,
               "note": "…the durable answer to the cold 7d path is a precomputed
                        movers artifact written pantry-side (cron + catalog entry
                        at birth) — a named follow-on lane, not this endpoint"}
```

`test_movers_grid_names_the_precompute_follow_on_rather_than_building_it` pins
that the payload keeps admitting it.

---

## 11. Notes for the dashboard lane

- **The three reserved v0.2 slots can be filled.** Ladder, heat grid and TB4
  all serve now.
- **Every ladder row is a range row today.** `vocabulary` tells you which shape
  you got; branch on that, never on a day count. The percentile branch is real,
  tested and lights itself as depth banks.
- **`chip` is absent, not null**, below the floor. `"chip" in row`.
- **`context` is server-supplied** — render it verbatim. It already obeys the
  depth grammar, so a surface that prints it blind still reads honestly.
- **`readFor` needs a metric argument** (see §4's wart) — a dashboard change,
  and `AN_STANCE_READS` must move with it.
- **Heat-grid cells align to `dates` positionally**; `null` is a hole in a real
  column. Rows are always the full HE1-24 axis so the grid keeps its shape while
  today fills in hour by hour.
- **`scale_cap` is a palette instruction.** Values are never clipped — HE20 of
  2026-07-24 arrives at **$1,019.10** and the cap is $15.
- **TB4 hub's newest day is tomorrow** (DAM is forward-looking). Caption it as
  the published day-ahead day, not "today".
- **Movers-grid: `value` and `cells` answer different questions.** `value` is
  the ranking statistic over the strict matched-hour set; a cell is that hour's
  mean over the dates it was banked, with its own `cell_n`. Under ragged
  coverage the row's cell mean need not equal `value` and neither is wrong —
  `value_definition` says so in the payload.
- **Default the movers-grid window to 1d**, same advice as movers, same reason,
  now with a real wall clock behind it.

---

## 12. Out of scope, untouched

Constraint correlation · Watchboard regime chips · the Cockpit TB4 tile ·
ERCOT · any Atlas change · the precompute artifact (§10) · the dashboard PR.
**No migrations.** No existing endpoint's behaviour changed; no existing test
modified. `main.py` is the only file edited (+1,332 / −16), `tests/test_regime.py`
is the only file added.
