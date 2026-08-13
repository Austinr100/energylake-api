# Handback — Queue Rollups + Generations Read (Lane A)

**Date:** 2026-08-13 · **Repo:** Austinr100/energylake-api · **Branch:** `claude/queue-rollups-api-v0`
**Files:** `interconnection.py` (+~330 lines, pure shaping) · `main.py` (+1 endpoint) ·
`tests/test_interconnection.py` (+44 tests, 153 total in file) ·
`tests/fixtures/caiso_queue_grains_2026_08_13.json` (new — the recon receipt, executed by tests)

Read-only. Two new `SELECT`s, no DDL, no writes, no migration. **No PR opened**
(compare link at the bottom). No dashboard-repo edits.

---

## 0. THE PINNED CONTRACT — `GET /api/interconnection/rollups`

**Pinned as of this commit and not moving.** Lane B can build against this.

```jsonc
{
  "snapshot_date": "2026-08-07",
  "data_note": "…",            // identical string to /summary's
  "rollup_note": "…",          // the grain rules, stated in the payload
  "generated_from_rows": 2278,

  "snapshot": {                // BYTE-IDENTICAL to /summary's `snapshot`
    "snapshot_date": "2026-08-07",
    "project_count": 2278,
    "total_mw": 492196.8,
    "statuses": [ {"status": "WITHDRAWN", "count": 1760, "mw": 381306.7}, … ]
  },

  "by_county": {
    "grain": "county",
    "key_count": 101,                       // 100 named + "unlocated"
    "missing_key": "unlocated",
    "active_statuses": ["ACTIVE"],
    "keys_with_zero_active": 58,
    "keys": ["KERN", "RIVERSIDE", …, "unlocated"],   // the canonical order
    "all_statuses": { "KERN": {"count": 370, "mw": 65200.5}, … },
    "active":       { "KERN": {"count": 42,  "mw": 11171.8}, …,
                      "MONO": {"count": 0,   "mw": 0.0}, … },
    "key_states":   { "KERN": ["CA"], "MARICOPA": ["AZ","CA"], … },
    "key_state":    { "KERN": "CA",   "MARICOPA": null,        … },
    "state_pairing": { "clean": false,
                       "ambiguous_keys": ["LINCOLN","MARICOPA","SAN BENITO","unlocated"],
                       "note": "MEASURED, not assumed: county -> state is NOT a function…" }
  },

  "by_state":   { …same block, grain "state",         key_count 8,  missing_key "unlocated"   },
  "by_process": { …same block, grain "study_process", key_count 23, missing_key "unspecified" },
  // by_state / by_process carry every key by_county does EXCEPT key_states /
  // key_state / state_pairing, which are county-only.

  "generations": {
    "source_table": "caiso_interconnection_queue_snapshots",
    "depth": 1,
    "snapshot_dates": ["2026-08-07"],
    "note": "depth 1 — exactly ONE generation is banked … That is a POINT, not a trend…",
    "entries": [
      { "snapshot_date": "2026-08-07",
        "rows": 2278,
        "mw_total": 492196.8,
        "by_status": { "WITHDRAWN": {"count": 1760, "mw": 381306.7},
                       "ACTIVE":    {"count": 268,  "mw": 75977.3},
                       "COMPLETED": {"count": 250,  "mw": 34912.8} } }
    ]
  },

  "cache": { "state": "…", "built_at": "…", "age_seconds": 0, "ttl_seconds": 900 }
}
```

Ordering rules Lane B can rely on:

* `keys` is descending `all_statuses` MW, ties by key string, decided on the
  **unrounded** value. `all_statuses` and `active` are built in that same order,
  so all three are zippable without re-sorting.
* `entries` is ascending `snapshot_date`.
* `by_status` inside a generation is descending MW.
* MW everywhere is rounded to 0.1, the same as v0.

### Why a new endpoint and not new keys on `/summary`

`/summary` **already** serves `by_county`, and it serves
`{top, other, group_count, top_n}` — a top-25 with an aggregated remainder. A
full 101-key rollup under that same key name would be a **reshape of a
load-bearing key**, not an addition, and the spec forbids exactly that. So the
full-cardinality grains live on their own surface under their own names.
`/summary`, `/projects` and `/events` are untouched — see §5.

---

## 1. Recon receipts (live, 2026-08-13, Energylake `fancy-block-96153928`)

### `information_schema.columns` — both tables

Both tables carry the **identical 21-column set**, same order, same types:
`queue_position text NOT NULL`, `project_name`, `generation_type`,
`capacity_mw numeric`, `status`, `study_process`, `interconnection_location`,
`county`, `state`, `transmission_owner` (all `text`, nullable),
`queue_date`/`proposed_completion_date`/`actual_completion_date`/`withdrawn_date`
(`date`, nullable), `withdrawal_comment text`, `deliverability text`,
`raw jsonb NOT NULL`, `present_in_snapshot boolean NOT NULL`,
`first_seen_at`/`last_updated` (`timestamptz NOT NULL`),
`snapshot_date date NOT NULL`.

### Indexes — `pg_index`, read not assumed

| table | index | definition |
|---|---|---|
| `caiso_interconnection_queue` | **PK** | `(queue_position)` |
| | | `(status)`, `(generation_type)`, `(present_in_snapshot)` |
| `caiso_interconnection_queue_snapshots` | **PK** | `(snapshot_date, queue_position)` |
| | | `caiso_iq_snapshots_position_idx (queue_position, snapshot_date)` |

Migration 178 is **APPLIED** in the database. Its `.sql` file is not in this
repo — `migrations/` here holds only `154_publications.sql`; the migration lives
in the pantry repo. Nothing in this lane needed it and no migration was written.

### Cardinalities — measured

| fact | value | matches the spec's number? |
|---|---:|---|
| `caiso_interconnection_queue` rows | 2,278 (all `present_in_snapshot`) | ✓ |
| total MW | 492,196.796 | ✓ |
| distinct `county` (non-null) | 100 | ✓ |
| `county IS NULL` rows | 5 · blank: **0** | ✓ |
| distinct `state` (non-null) | 7 · `state IS NULL` rows: 4 · blank: 0 | ✓ |
| distinct `study_process` (non-null) | 22 | ✓ |
| **`study_process IS NULL` rows** | **1** (520.0 MW) | ⚠︎ not in the spec — see §2 |
| distinct `(county, state)` pairs | **106** vs 100 counties | the ambiguity, see §3 |
| `capacity_mw IS NULL` | 0 | — |

Resulting key counts, confirmed by executing the shipped read verbatim:
**county 101 · state 8 · study_process 23.**

### `caiso_interconnection_queue_snapshots`

One generation banked: `2026-08-07`, 2,278 rows, 2,278 distinct
`queue_position`, 492,196.796 MW, 0 null capacity, all `present_in_snapshot`,
3 statuses, 0 null status. **depth = 1.**

---

## 2. One thing the spec did not name: a NULL `study_process`

The spec says `by_process` carries "all 22 keys". The bank holds 22 distinct
non-null values **and one row with `study_process IS NULL`** (520.0 MW). Dropping
it would have been a silent loss of a real project; folding it into a real
cluster would have been an invention. It gets the same treatment `unlocated`
gets at the geography grains: **a named key, `"unspecified"`**, counted with its
MW, sitting in the ordering with everybody else. So `by_process.key_count` is
**23**, not 22, and `missing_key` names the convention in the payload.

Blank-but-not-null is handled identically (`None` and all-whitespace both map to
the missing key). There are **0** blank values in county/state/study_process
today, so that branch is dormant and correct anyway.

---

## 3. County → state is NOT clean. Measured, and not resolved by picking.

The spec said to measure rather than assume. **It is ambiguous.** 100 named
counties produce 106 `(county, state)` pairs:

| county | states it appears under | rows per state |
|---|---|---|
| `LINCOLN` | ID, NM, NV | 1 / 1 / 1 |
| `MARICOPA` | AZ, CA | 37 / 1 |
| `SAN BENITO` | CA, NV | 5 / 1 |
| *(null county → `unlocated`)* | CA, *(null → `unlocated`)* | 1 / 4 |

County names are not unique across the seven states this queue spans, so that is
CAISO being correct, not CAISO being dirty. Per the spec's instruction, no state
is picked. The payload carries instead:

* **`key_states`** — always a list, every state observed for that county key.
* **`key_state`** — the state **only** where there is exactly one; `null` for the
  four keys above. A null here means "spans more than one state", never
  "unknown", because `key_states` always names them.
* **`state_pairing.note`** — says it in words and names the ambiguous keys.

The block is keyed on **county alone**, not on `(county, state)`. A compound key
would have silently split `MARICOPA` into two counties on a page that asked for
counties. `by_state` is the independent state-grain answer.

The note is derived, not hard-coded: hand the shaper a bank where every county
pairs cleanly and `state_pairing.clean` flips to `true` and every `key_state`
populates, with no code change (`test_the_pairing_note_flips_to_clean_when_the_bank_is`).

---

## 4. EXPLAIN timing on the generations read — and the trap it did **not** spring

The spec predicted this read would be index-friendly because the PK leads on
`snapshot_date`. **Measured, it is not, and it does not matter.**

`EXPLAIN (ANALYZE)` on the shipped statement, 2026-08-13:

```
Sort  (cost=473.63..473.64 rows=3)  actual time=1.373..1.374 rows=3
  Sort Key: snapshot_date, status          Sort Method: quicksort  Memory: 25kB
  -> HashAggregate  (actual time=1.363..1.365 rows=3)
       Group Key: snapshot_date, status    Batches: 1  Peak Memory: 24kB
       -> Seq Scan on caiso_interconnection_queue_snapshots
            (cost=0.00..450.78 rows=2278) actual time=0.007..0.303 rows=2278
Shared Hit Blocks: 428 (0 read)
Planning Time: 0.761 ms      Execution Time: 1.407 ms
```

**Seq Scan, and the planner is right.** The aggregate has to touch every row of
every generation — there is no predicate to seek on — and `capacity_mw` is not in
either index, so an index scan would add a heap fetch per row on top of the same
work. The PK's `snapshot_date` prefix buys nothing for a full-table `GROUP BY`.

**The D-08-02-L trap watch.** The read is `O(depth × 2,278)`: ~428 shared blocks
and ~1.4 ms per generation. A year of weekly Friday snapshots is ~118k rows /
~22k blocks — still a low-tens-of-ms aggregate, and it is cached 15 minutes. The
statement is also **reduced in the database** (`GROUP BY snapshot_date, status`
returns `depth × statuses` rows — 3 today), not fetched row-by-row and grouped in
Python, so the wire cost stays flat as the bank deepens. If it ever stops being
cheap the lever is a covering index on `(snapshot_date, status, capacity_mw)` —
that is a migration, and therefore explicitly **not this lane's to make**. Named
here so the captain has it.

For completeness, the rollups read over the live table:
`Seq Scan on caiso_interconnection_queue`, filter `present_in_snapshot`,
864 shared blocks, **1.601 ms execution / 0.085 ms planning**, 2,278 rows.

### Live verification of both shipped statements

Executed verbatim against production over the Neon HTTPS path:

* rollups read → 2,278 rows, `capacity_mw` type `double precision`,
  **101 county keys / 8 state keys / 23 process keys**, 492,196.796 MW ✓
* generations read → the 3 aggregate rows above, `snapshot_date` 2026-08-07,
  268/250/1,760 and 75,977.3334 / 34,912.7874 / 381,306.6747 MW ✓

Not covered by a live run: FastAPI wiring end-to-end against a real pool. That
path is covered by the `_FakePool` route tests and is the same acquisition path
every other endpoint in this repo uses.

---

## 5. Every pre-existing key is byte-stable — confirmed, not asserted

**Nothing on `/summary`, `/projects` or `/events` was renamed, reshaped or
removed.** Three mechanisms back that up:

1. **The 109 v0 tests were not modified and all still pass.** They pin
   `/summary`'s every block, the mapping table value by value, the attrition
   arithmetic, pagination, and the injection posture.
2. **New pinning tests added on purpose:**
   * `test_v0_top_level_keys_are_unchanged_by_this_commit` — asserts the exact
     top-level key **sets** of all three v0 responses.
   * `test_summary_by_county_keeps_its_v0_top_n_shape` — `/summary.by_county` is
     still `{top, other, group_count, top_n}` with `top_n == 25` and
     `{county, state, count, mw}` rows.
   * `test_the_extracted_snapshot_helper_did_not_move_summary` — full value
     equality on the `snapshot` block, including key **order**.
3. **The only edit inside v0 code paths is a pure extraction.** The `snapshot`
   block was lifted out of `summary_payload` into `_snapshot_block(rows,
   snapshot_date)` so `/rollups` serves the *same* block from the *same* helper —
   which is spec item 5, "no module can drift from its neighbor". Same
   expressions, same order, same output. `test_the_rollup_snapshot_chip_is_the_same_block_summary_serves`
   asserts the two endpoints return an equal object.

Three tests were **extended** (not weakened): `_FakePool` gained a branch for the
snapshots aggregate, the read-only test and the table-scope test now sweep all
four routes. The table-scope allowance widened from two tables to three, and
`test_the_snapshots_table_is_read_by_nothing_but_generations` proves the third
one is touched by `/rollups` alone.

---

## 6. Rules upheld

**No coordinates, no geocoding, no location inference.** County and state ride
out as the strings the bank holds. `USA`, `KINGS/KERN`, `L.A`, `SAN BERNADINO`
(sic), `ALAMEDA COUNTY` and `MERCED / FRESNO` are all real county values in this
table and all ride out unchanged — no title-casing, no splitting on `/`, no
lookup, no repair. Two tests pin that (`test_county_strings_ride_out_exactly_as_banked`).
Same for the 22 raw `study_process` strings: `AMEND 39`, `Pre- Amend. 39`,
`Serial LGIP`, `SGIP-TC` and the `C01..C14` cluster labels are CAISO's vocabulary
and are not normalized here.

**Nothing folded, nothing truncated.** There is no `other` bucket at any grain
and no top-N. `key_count` is published so a client can verify the cardinality
itself, and `test_nothing_is_dropped_the_grain_partitions_the_bank` asserts the
counts sum back to the whole bank at every grain.

**Dimmed at zero, not missing at zero.** `active` carries **every** key
`all_statuses` carries, in the same order. **58 of the 101 counties have zero
ACTIVE rows** and appear as `{"count": 0, "mw": 0.0}`; so do 4 of 8 states and 2
of 23 processes. `keys_with_zero_active` counts them. A vanished key would read
as "no such county"; a zeroed key reads as "nothing live here".

**Rollups from the live table, generations from snapshots.** The three grain
blocks are the current generation, read from `caiso_interconnection_queue`.
`generations` is the only block sourced from
`caiso_interconnection_queue_snapshots`, and no v0 route touches that table.

**depth-1 is stated, not drawn.** `generations.depth` is a first-class field and
the note says "a POINT, not a trend" in words. Nothing interpolates, back-fills,
or borrows a second point from the upsert-in-place queue table. A second banked
generation turns this into a series with no code change
(`test_generations_are_ordered_ascending_and_gaps_are_not_interpolated` runs the
depth-3 case; `test_an_empty_snapshots_table_is_an_honest_depth_zero` runs
depth 0).

---

## 7. Tests — 153 in the file (44 new), all green

Full repo suite: **1,514 passed, 4 failed.** The 4 failures
(`test_almanac.py` ×3, `test_chart_brief.py` ×1) are **pre-existing on `main`** —
verified by running those two files on `main` before this branch, identical 4
failures. Untouched by this lane.

The new coverage, by what it defends:

* **Nothing folded** — every key at every grain, parametrized ×3; explicit
  assertions that no `other`/`top`/`top_n` key exists in a rollup block; the
  grain partitions the bank (counts and MW).
* **Missing is named** — `unlocated` for null county and null state, `unspecified`
  for null **and** blank `study_process`; the null-county rows split across two
  state keys without either grain inventing the other's value.
* **Dimmed at zero** — `active` key set equals `all_statuses` key set at every
  grain, zeros asserted key by key, `keys_with_zero_active` checked.
* **Key order** — `keys == list(all_statuses) == list(active)`; and ordering is
  proved to run on the **unrounded** MW (`KINGS/KERN` 20.0 outranks `MONO` 19.993
  though both print as 20.0).
* **State pairing** — ambiguous county gets `key_state: null` + full `key_states`;
  unambiguous county gets its state; the note flips to `clean: true` on a clean
  bank; the `unlocated` key is itself ambiguous and says so.
* **Keyed on county alone** — `MARICOPA` is one key with two states behind it.
* **generations** — depth 1 / 0 / 3; ascending order from shuffled input; gaps not
  interpolated; `by_status` reconciles to `rows` and `mw_total`; statuses ordered
  by MW.
* **THE RECON RECEIPT, EXECUTED** — `tests/fixtures/caiso_queue_grains_2026_08_13.json`
  holds all 101 county / 8 state / 23 process keys with their live count, MW,
  active count and active MW. `test_the_live_grain_reproduces_key_for_key`
  rebuilds a row set from it and asserts `grain_block` reproduces every key's
  numbers exactly, plus the key counts and the zero-active counts. A folded key,
  a dropped NULL or a truncated tail moves this test. `test_the_ambiguous_county_keys_are_exactly_the_four_the_bank_holds`
  does the same for the pairing.
  *(One tolerance, stated: per-key MW is rounded to 0.1 before summing, so
  re-summing 101 keys lands 0.8 MW off the live 492,196.796. The COUNT check
  beside it is exact and is what proves nothing was dropped.)*
* **Route** — the contract keys; the chip equals `/summary`'s; the read pulls
  neither fat column nor `generation_type`; the generations read is reduced in
  the DB; the snapshots table is invisible to the three v0 routes.
* **v0 byte-stability** — §5.

---

## 8. Compare link

https://github.com/Austinr100/energylake-api/compare/main...claude/queue-rollups-api-v0
