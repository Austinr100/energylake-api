# Handoff — Interconnection Queue API v0 (Lane C of 2)

**Date:** 2026-08-12 · **Repo:** Austinr100/energylake-api · **Branch:** `claude/interconnection-api-v0`
**Files:** `interconnection.py` (new, pure shaping) · `main.py` (routes, +3 endpoints) ·
`tests/test_interconnection.py` (new, 109 tests) ·
`tests/fixtures/caiso_generation_types_2026_08_07.json` (new)

Read-only. Three `SELECT`s, no DDL, no writes, no migration, nothing touched outside
`caiso_interconnection_queue` and `tape_interconnection_events`. A test asserts both
of those properties over every statement the three routes send.

---

## 1. Recon — what the bank actually holds

Schema read from `information_schema.columns` before any query was written; every
number below is a live read on **2026-08-12** against the `Energylake` Neon project
(`fancy-block-96153928`).

### `caiso_interconnection_queue`

| fact | value |
|---|---|
| rows | 2,278 (all `present_in_snapshot = true`) |
| capacity | 492,196.8 MW |
| `snapshot_date` | 2026-08-07 — and **only** that one; `count(DISTINCT snapshot_date) = 1` |
| `queue_date` span | 1999-11-01 .. 2023 |
| distinct `generation_type` | 41 |
| `withdrawn_date` present | 1,721 rows |
| `first_seen_at` | **identical on all 2,278 rows** (2026-06-11T01:03:57.801Z) |
| `last_updated` | identical on all rows (2026-08-07T21:21:09.093Z) |
| nulls | `proposed_completion_date` 255 · `county` 5 · `state` 4 · `transmission_owner` 3 · `generation_type` 0 · `capacity_mw` 0 · `queue_date` 0 |
| other cardinalities | 100 counties / 106 (county,state) groups · 7 states + null (AZ, CA, ID, MX, NM, NV, WY) · 8 transmission owners + null · 22 study processes · 3 deliverability values + null |

**The finding that shapes the lane: this table is upsert-in-place, not an
append-only snapshot log.** One `snapshot_date`, one `first_seen_at`, one
`last_updated` across the whole table. `first_seen_at` is served on every project
row as the contract requires, but today it *cannot distinguish a new entrant from
a founding row* — it records the bulk birth of the table, not the moment a project
appeared in CAISO's queue. The `data_note` says exactly this, derived from the read
rather than hard-coded: the clause disappears on its own the day a second
generation is banked. See §6.

### Status values found

Three, exactly as expected:

| status | rows | MW | rows with `withdrawn_date` |
|---|---:|---:|---:|
| `WITHDRAWN` | 1,760 | 381,306.7 | 1,721 |
| `ACTIVE` | 268 | 75,977.3 | 0 |
| `COMPLETED` | 250 | 34,912.8 | 0 |

**1,760 ≠ 1,721.** 39 rows / 10,857.1 MW carry `status = 'WITHDRAWN'` with **no**
`withdrawn_date`. The lane rules `withdrawn = withdrawn_date IS NOT NULL`, so those
39 are *not* withdrawn to the attrition series — they cannot be placed on a
per-vintage timeline without a date. They are not absorbed silently: the attrition
block reports them as `withdrawn_status_without_date: {count, mw, note}` beside the
series. (The converse never happens: 0 rows carry a `withdrawn_date` without
`status = 'WITHDRAWN'`.)

### Tape verdict: **POPULATED**

`tape_interconnection_events` holds **8 rows**, every one `event_type =
'status_change'`, detected 2026-06-12 .. 2026-07-31 — six `ACTIVE → WITHDRAWN`
and two `ACTIVE → COMPLETED`. So `/events` serves real rows. The honest-empty
branch is built and unit-tested, but it is not the live path.

This ledger is where queue lineage actually lives today, precisely because the
queue table is upsert-in-place and cannot recover a project's history.

### Cadence — measured, not assumed

Every banked `snapshot_date` across both tables — 2026-06-12, 06-26, 07-17, 07-24,
07-31 (tape) and 08-07 (queue) — is a **Friday**, 8 distinct dates over 8 weeks
with three gaps (06-19, 07-03, 07-10 missing). `data_note` says "weekly, on
Fridays, with gaps", with the dates. It does not say "daily", and it does not say
"weekly" without the qualifier.

### Router conventions followed

There is no `APIRouter` anywhere in this repo — every endpoint is `@app.get(...)`
directly on the `FastAPI` app in `main.py` (verified: `grep APIRouter` returns
nothing across 16k lines). The house idiom is a **pure sidecar module** beside
`main.py` (`degree_days.py`, `almanac.py`, `structures.py`, `paper_desk.py`,
`paper_lab.py`) holding shaping with no DB handle, no clock and no FastAPI, while
`main.py` owns SQL, cache and routes. `interconnection.py` conforms exactly.
Reused verbatim rather than reinvented: the `_DDCache` single-flight cache and
`_dd_envelope` cache chip (`main.py:15404`, `:15505`), the retry-once-on-stale-
connection read shape (`_cockpit_read`, `main.py:7981`), the optional-filter idiom
`%(p)s::type IS NULL OR ...`, and `_cockpit_like_escape` (`main.py:7803`).

---

## 2. The three endpoints

### `GET /api/interconnection/summary`

Every aggregate in one call, off **one read**.

```
{ snapshot_date, data_note, generated_from_rows,
  snapshot:              { snapshot_date, project_count, total_mw,
                           statuses: [{status, count, mw}] },
  by_fuel:               { active: {fuel -> {count, mw}},   # Other also has raw_types
                           all_statuses: {fuel -> {count, mw}},
                           active_statuses: ["ACTIVE"] },
  by_queue_year:         [{year, count, mw}],
  by_proposed_cod_year:  { years: [{year, count, mw}], undated: {count, mw} },
  by_county:             { top: [{county, state, count, mw}], other: {count, mw, group_count},
                           group_count, top_n },
  by_transmission_owner: [{to, count, mw}],
  attrition:             { years: [{year, enqueued_count, enqueued_mw,
                                    withdrawn_count, withdrawn_mw,
                                    surviving_count, surviving_mw,
                                    withdrawn_mw_share}],
                           totals, no_queue_date,
                           withdrawn_status_without_date, definition },
  unmapped_types:        [{generation_type, fuel, unmapped_components, known, count, mw}],
  cache:                 { state, built_at, age_seconds, ttl_seconds, ... } }
```

**Why one read and not six `GROUP BY`s.** The fuel rollup is a parse over free
text, so `by_fuel` can never be a `GROUP BY`. Once one facet is computed in
process, computing the others in SQL would let two facets of the same response
describe two different universes. 2,278 slim rows (no `raw` jsonb, no
`withdrawal_comment` — a test asserts the fat columns are never selected) is a
single ~1 MB read; every facet is derived from it, so they cannot disagree.
Cached 15 min against a bank that moves weekly.

### `GET /api/interconnection/projects`

All contract params honored: `status`, `fuel`, `county`, `to`, `min_mw`, `max_mw`,
`queue_year`, `q`, `sort`, `page`, `page_size` (≤ 100, enforced by FastAPI → 422).
`q` is a case-insensitive substring over `project_name` **and** `queue_position`,
with LIKE metacharacters escaped so they match literally. `fuel` filters on the
**rollup**, not on raw `generation_type`.

Every row carries **both** raw `generation_type` and rollup `fuel` (plus
`unmapped_components`), `first_seen_at`, `withdrawn` / `withdrawn_date` /
`withdrawal_comment`, and the full identity + location + date set.

### `GET /api/interconnection/events?limit=`

Newest-first tape rows, `limit` ≤ 500. `changed_fields` is derived from the keys of
`old_values`/`new_values` so a client can name what moved without diffing two jsonb
blobs. Empty ledger → 200 with `rows: []`, `count: 0`, and a `note` saying so —
never a 404, and nothing reconstructed from the queue snapshot or inferred from a
`withdrawn_date`.

---

## 3. The fuel rollup — the mapping table **as shipped**

All 41 live strings. Keyword match is case-insensitive **per component**, splitting
on `+` first — a bare substring scan would collide "Wind Turbine" with "Combustion
Turbine". Every row below is pinned by `test_the_mapping_table_as_shipped`.

| # | raw `generation_type` | → fuel | n | MW | unmapped component |
|---:|---|---|---:|---:|---|
| 1 | Photovoltaic | **Solar** | 693 | 81,330.0 | |
| 2 | Solar Thermal | **Solar** | 3 | 705.0 | |
| 3 | Storage | **Battery Storage** | 485 | 98,010.7 | |
| 4 | Storage + Storage | **Battery Storage** | 4 | 3,292.0 | *(R2)* |
| 5 | Wind Turbine | **Wind** | 189 | 52,783.2 | |
| 6 | Photovoltaic + Storage | **Solar+Storage Hybrid** | 243 | 53,719.2 | |
| 7 | Storage + Photovoltaic | **Solar+Storage Hybrid** | 178 | 56,150.2 | |
| 8 | Storage + Solar Thermal | **Solar+Storage Hybrid** | 1 | 200.0 | |
| 9 | Storage + Photovoltaic + Wind Turbine | **Solar+Storage Hybrid** | 3 | 550.0 | *(R3)* |
| 10 | Storage + Wind Turbine + Photovoltaic | **Solar+Storage Hybrid** | 3 | 515.0 | *(R3)* |
| 11 | Wind Turbine + Photovoltaic + Storage | **Solar+Storage Hybrid** | 2 | 600.0 | *(R3)* |
| 12 | Wind Turbine + Storage + Photovoltaic | **Solar+Storage Hybrid** | 2 | 400.0 | *(R3)* |
| 13 | Photovoltaic + Wind Turbine + Storage | **Solar+Storage Hybrid** | 1 | 200.0 | *(R3)* |
| 14 | Photovoltaic + Storage + Combustion Turbine | **Solar+Storage Hybrid** | 1 | 538.0 | *(R3)* |
| 15 | Wind Turbine + Storage | **Other Hybrid** | 15 | 9,343.7 | |
| 16 | Storage + Wind Turbine | **Other Hybrid** | 9 | 8,253.7 | |
| 17 | Steam Turbine + Storage | **Other Hybrid** | 6 | 1,066.3 | Steam Turbine |
| 18 | Combustion Turbine + Storage | **Other Hybrid** | 4 | 2,281.8 | |
| 19 | Photovoltaic + Combustion Turbine | **Other Hybrid** | 4 | 1,047.0 | |
| 20 | Combustion Turbine + Photovoltaic | **Other Hybrid** | 3 | 800.0 | |
| 21 | Gas Turbine + Storage | **Other Hybrid** | 3 | 1,004.0 | |
| 22 | Photovoltaic + Wind Turbine | **Other Hybrid** | 3 | 1,669.0 | |
| 23 | Storage + Gas Turbine | **Other Hybrid** | 3 | 658.1 | |
| 24 | Storage + Other | **Other Hybrid** | 2 | 1,000.0 | Other |
| 25 | Combined Cycle + Storage | **Other Hybrid** | 1 | 120.0 | |
| 26 | Combustion Turbine + Storage + Steam Turbine | **Other Hybrid** | 1 | 910.0 | Steam Turbine |
| 27 | Photovoltaic + Steam Turbine | **Other Hybrid** | 1 | 210.0 | Steam Turbine |
| 28 | Storage + Combustion Turbine | **Other Hybrid** | 1 | 444.0 | |
| 29 | Storage + Steam Turbine + Combustion Turbine | **Other Hybrid** | 1 | 910.0 | Steam Turbine |
| 30 | Wind Turbine + Photovoltaic | **Other Hybrid** | 1 | 150.0 | |
| 31 | Wind Turbine + Storage + Storage | **Other Hybrid** | 1 | 600.0 | |
| 32 | Combined Cycle | **Natural Gas** | 94 | 39,958.3 | |
| 33 | Gas Turbine | **Natural Gas** | 61 | 13,804.2 | |
| 34 | Combustion Turbine | **Natural Gas** | 57 | 13,085.6 | |
| 35 | Reciprocating Engine | **Natural Gas** | 15 | 1,937.6 | |
| 36 | Cogeneration | **Natural Gas** | 3 | 91.5 | |
| 37 | Combined Cycle + Combined Cycle | **Natural Gas** | 1 | 48.3 | *(R2)* |
| 38 | Hydro | **Hydro** | 12 | 4,334.6 | |
| 39 | Steam Turbine | **Other** | 158 | 38,318.3 | Steam Turbine *(R1)* |
| 40 | Other | **Other** | 8 | 1,130.6 | Other |
| 41 | Steam Turbine + Steam Turbine | **Other** | 2 | 27.2 | Steam Turbine *(R2)* |

**Resulting rollup over the live bank (2026-08-07, all statuses):**

| fuel | count | MW |
|---|---:|---:|
| Solar | 696 | 82,035.0 |
| Battery Storage | 489 | 101,302.7 |
| Wind | 189 | 52,783.2 |
| Solar+Storage Hybrid | 434 | 112,872.4 |
| Other Hybrid | 59 | 30,467.6 |
| Natural Gas | 231 | 68,925.5 |
| **Geothermal** | **0** | **0.0** |
| Hydro | 12 | 4,334.6 |
| Other | 168 | 39,476.1 |
| **total** | **2,278** | **492,197.1** ¹ |

¹ 0.3 MW above the live 492,196.8 because the fixture banks each type's MW rounded
to 0.1 and 41 roundings accumulate. The API computes from unrounded values and
reports 492,196.8; the test asserts the fixture within ±0.5 and says why.

### Unmapped is visible, never swallowed

* `by_fuel[*].Other.raw_types` — every raw string that landed in `Other`, with its
  own count and MW. A reader seeing 39.5 GW of Other sees immediately that
  38.3 GW of it is the literal string "Steam Turbine".
* `unmapped_types` (top level, on **both** `/summary` and `/projects`) — every raw
  string containing a component no keyword recognised, *whatever bucket it landed
  in*. This is bucket-independent on purpose: `Steam Turbine + Storage` rolls up to
  Other Hybrid and would be invisible if we only looked inside `Other`. Eight of
  the 41 strings appear here.
* Each entry carries `known: true|false` against the 41 banked at recon time.
  **A 42nd string appearing in the pantry tomorrow shows up as `known: false` in
  the response body that same day, with no deploy.** `interconnection.KNOWN_GENERATION_TYPES`
  is the drift sensor; `tests/fixtures/caiso_generation_types_2026_08_07.json` is
  its transcript, and a test asserts the two are the same set.
* `Geothermal` matches zero rows and is still emitted at count 0. An empty bucket
  is a fact about the queue; a missing bucket would be a fact about our code.

---

## 4. Three judgment calls, named

**R1 — "Steam Turbine" is NOT mapped to Natural Gas.** 158 rows, 38,318.3 MW — the
sixth-largest raw string in the bank. CAISO writes "Steam Turbine" for gas steam,
geothermal, biomass and solar-thermal blocks alike; the column records a prime
mover, not a fuel. Calling it Natural Gas would manufacture 38 GW of fuel knowledge
the data does not contain. It maps to `Other` with its raw label attached, which is
the honest shape of "we know the prime mover, not the fuel". **If you want it as
gas, it is one line in `_COMPONENT_KEYWORDS` plus one line in the pinned test.**

**R2 — DEVIATION FROM THE LITERAL SPEC.** The lane says *"Other Hybrid (any other
multi-type string)"*. Read literally that sends `Storage + Storage` (4 rows,
3,292 MW), `Steam Turbine + Steam Turbine` and `Combined Cycle + Combined Cycle`
into a hybrid bucket they do not belong in — those are duplicate-component
artifacts of CAISO's own string building, not mixed-technology projects. So
"multi-type" is implemented as **more than one *distinct* component**, and
`Storage + Storage` rolls up to Battery Storage. Affects 7 rows / 3,367.5 MW total.
Say the word and it reverts to the literal reading in one line.

**R3 — the "contains both" rule is applied literally, including with a third
technology.** The lane says Solar+Storage Hybrid is *"any multi-type string
containing both"*. So `Storage + Photovoltaic + Wind Turbine` and
`Photovoltaic + Storage + Combustion Turbine` are Solar+Storage Hybrid, not Other
Hybrid — 12 rows / 2,803.0 MW where a wind or gas component rides inside the
Solar+Storage bucket. This follows the spec as written; flagging it because it is
visible in the table above and is the kind of thing that looks like a bug later.

---

## 5. Doctrine compliance

**Snapshot chip in every response.** `snapshot_date` + `data_note` on `/summary`
and `/projects`; `/events` carries per-row `snapshot_date` and `detected_at`. All
three carry the `cache` chip (`state`, `built_at`, `age_seconds`, `ttl_seconds`)
plus `X-Cache` / `Age` / `Cache-Control` headers, reusing `_dd_envelope`.

**`data_note` states cadence as measured.** It names the eight Friday snapshot
dates, says "weekly, on Fridays, with gaps", states the upsert-in-place finding and
its consequence for `first_seen_at`, and states that no coordinates exist.
It is **assembled from the read**, not a constant — `_ic_data_note()` branches on
what `_ic_snapshot_chip()` actually found, so the `first_seen_at` caveat removes
itself when a second generation is banked (tested both ways).

**Absence stated.** Null proposed CODs are `undated` with their own count and MW —
never dropped, never zero-dated, never folded into a year. Null `county` / `state` /
`transmission_owner` are their own buckets, not dropped rows. A row with no
`queue_date` is reported as `attrition.no_queue_date` (0 rows today, but the path
exists). `by_county` is a top-25 with the remainder aggregated as `other` **and**
the full `group_count` (106) stated — a bounded list, never a silent truncation.
A null `capacity_mw` stays null on a row and contributes 0.0 to a sum, with the
count beside every MW total to say how many projects stand behind it.

**No invented geography.** The bank holds no coordinates. No endpoint returns,
derives, or implies a position beyond `county` / `state`, and `data_note` says so.

**SQL injection posture.** Every filter is a bound parameter under the house
`%(p)s::type IS NULL OR ...` idiom — one statement, one plan, no string building.
`sort` and `order` are **keys** into code-side constant tables of SQL fragments
(`_IC_SORT_SQL`); a value outside either whitelist is a **400 naming the
whitelist**, never a silent fallback to the default (a silent fallback would let a
mistyped or hostile sort look like it worked). There is no path from a request
string into the statement text. Nine hostile `sort` values are tested — including
`capacity_mw; DROP TABLE caiso_interconnection_queue`, `raw` (a real column, but
off-whitelist) and `CAPACITY_MW` (the whitelist is exact, not case-folded) — each
asserted to 400 **and** to send zero SQL. A separate test asserts no statement ever
contains `DROP TABLE`, and another feeds a hostile `county` value and asserts it
travels as a parameter and never appears in the SQL text.

**Read-only, no DDL.** A test walks every statement the three routes issue and
asserts each starts with `SELECT`/`WITH` and contains no `INSERT`/`UPDATE`/
`DELETE`/`DROP`/`ALTER`/`CREATE`/`TRUNCATE`/`GRANT`; another asserts no table
outside the two banked ones is named.

---

## 6. Contract fields I could not fully honor — named

**1. Snapshot lineage via `first_seen_at` does not yet discriminate.** The lane
names `first_seen_at` / withdrawn tracking as our differentiator against
interconnection.fyi. `first_seen_at` **is served on every project row** as
specified — but all 2,278 rows share one value, because
`caiso_interconnection_queue` is upsert-in-place with a single banked generation.
It records when the table was built, not when a project entered the queue. The
endpoint does not pretend otherwise: no "new this week" facet was built on top of
it, and `data_note` states the limitation as measured. **The differentiator is real
but it lives in `tape_interconnection_events` today**, which is why `/events`
carries the full old/new diff rather than just a headline. Withdrawn tracking, the
other half, is fully honored — `withdrawn_date`, `withdrawal_comment`, the derived
`withdrawn` boolean, and the whole `attrition` survival view. **Nothing is needed
from this repo to fix the first half; it needs the pantry's ingester to bank
successive snapshots.**

**2. "Active statuses" was undefined; I ruled and made the ruling explicit.**
`by_fuel.active` uses `ACTIVE` only — COMPLETED projects are built and WITHDRAWN
ones are gone, so neither is queue pipeline. The response publishes
`by_fuel.active_statuses: ["ACTIVE"]` so Lane D never has to guess, and
`all_statuses` is there as specified.

**3. One param added beyond the contract: `order=asc|desc`.** `sort` alone cannot
express direction, and a fixed direction per column would make "smallest projects
first" unreachable. It is optional and defaults per column (desc for MW and dates,
asc for `project_name`), so every contract-shaped request behaves as specified.
It is whitelisted on the same footing as `sort`.

Everything else in the contract is delivered verbatim: all seven `/summary`
aggregate blocks with the specified keys, all ten `/projects` params, the
`page_size ≤ 100` cap, the four-value `sort` whitelist, raw + rollup fuel on every
row, and `/events?limit=`.

---

## 7. Tests — 109, all green (full repo suite: 1,377 passed)

* **Rollup covers all 41 values** — one parametrized case per live string asserting
  it maps into the published vocabulary, plus `test_the_mapping_table_as_shipped`
  pinning every single value → fuel (the table in §3). A keyword change that moves
  any string moves this test, which is the point.
* **Fixture ↔ module drift** — the checked-in 41 values are asserted to equal
  `KNOWN_GENERATION_TYPES`, and to sum to 2,278 rows / 492,196.8 MW.
* **Nothing dropped** — all 41 strings at their live multiplicities rebuilt into
  2,278 rows; `sum(by_fuel.count) == project_count == 2278`.
* **Attrition hand-checked for the 2021 vintage** — live: 361 projects,
  105,537.135 MW enqueued, 253 / 70,222.325 MW withdrawn. The test asserts the
  hand-computed complement (108 projects, 35,314.810 MW surviving) and the
  hand-computed share (0.6654). Plus: the 1999 vintage reports withdrawn 0 / 0.0
  (a measurement, not an absence); the share is null **only** when the denominator
  is zero; and the 39-row status/date disagreement is counted, not absorbed.
* **Pagination bounds** — six parametrized cases including the ragged last page and
  a page past the end (empty `rows`, truthful `total`, no 404 and no clamp); an
  empty result set; and a partition test proving three pages reassemble the set
  exactly.
* **Hostile sort rejected** — see §5.
* Plus: unseen-value drift alarm, null/blank `generation_type`, the
  `+`-split turbine-collision guard, case-insensitivity, `undated` CODs,
  county top-N + remainder arithmetic, the derived `data_note` both ways,
  honest-empty events, read-only, and table scope.

### Live verification

Direct Postgres (:5432) is blocked from the session container by the environment's
network policy — HTTPS only — so the app could not be booted against the pantry
from here. Instead **all four statements were executed against production over the
Neon HTTPS path**, with the optional-filter predicates exercised in both branches:

* summary read → 2,278 rows / 492,196.8 MW ✓
* snapshot chip read → `snapshot_date` 2026-08-07, `snapshot_count` 1, `first_seen_at` min = max ✓
* projects read, **all filters populated** (status + county + TO + min_mw + max_mw + queue_year + escaped `q`) → parses, plans, returns ✓
* projects read, **all filters NULL** (the browse case) → 2,278 rows, confirming the `::type IS NULL` idiom short-circuits correctly ✓
* projects read with `q='%solar%'` and `ORDER BY capacity_mw DESC NULLS LAST` → correct rows in correct order ✓
* events read → the 8 tape rows, newest first ✓

The `ESCAPE '\'` clause, the `extract(year FROM queue_date)` predicate and both
`ORDER BY` branches were included in those executions. What is **not** covered by
a live run: the FastAPI wiring end-to-end against a real pool (route → cache →
`_ic_read` → shaping). That path is covered by the route tests against the
`_FakePool` idiom, and it is the same acquisition path every other endpoint in
this repo uses.

---

## 8. Compare link

https://github.com/Austinr100/energylake-api/compare/main...claude/interconnection-api-v0
