# Handoff — The Paper Desk v0 (`energylake-api`)

**Date:** 2026-07-31
**Branch:** `claude/blotter-book-equity-schema-gqwghp`, branched off `main` at
`8f187da` (after the Regime Package, PR #54, merged)
**House law followed:** `docs/handoff_2026_07_30_regime_package_v0_api.md` and
the Nodes lane before it — pure-layer/route split (as `structures.py`), fake-pool
tests, honest empty/thin states, EXPLAIN receipt per new query shape.

**Scope shipped:** all five stanzas + 80 new tests.
Suite: **938 passed** (80 new, 858 pre-existing, **none modified**).
**No migrations. No new indexes. No existing endpoint changed.** One new query
shape, EXPLAIN'd below.

---

## 1. What shipped

| # | endpoint | new query shapes |
|---|---|---|
| 1 | `GET /api/desk/blotter` | 1 (shared by all five) |
| 2 | `GET /api/desk/book` | 0 — rides #1 |
| 3 | `GET /api/desk/equity` | 0 — rides #1 |
| 4 | `GET /api/desk/by-node` | 0 — rides #1 |
| 5 | `GET /api/desk/by-play` | 0 — rides #1 |

New files: `paper_desk.py` (the pure ledger layer — no I/O, no clock, no
database) and `tests/test_paper_desk.py`. `main.py` gains the five routes, one
SQL constant and one reader; `README.md` gains a Paper Desk section.

---

## 2. Depth report, measured 2026-07-31

`paper_journal`, full table:

| kind | rows | span (trade_date) | settled |
|---|---|---|---|
| `note` | 17 | 2026-07-12 … 2026-07-30 | n/a — not positions |
| `bid`  | 2  | 2026-07-30 | **0** |
| **total** | **19** | | |

Both bids, filed `2026-07-30T12:47:48Z`:

| entry_id | pnode_id | side | MW | price_limit | hour_scope | conviction | status | frontier_ok |
|---|---|---|---|---|---|---|---|---|
| 18 | `LUNDY_7_N003` | DEC | 10 | $20.68 | HE7-22 | 4 | **OPEN** | **false** |
| 19 | `CONTROLX_1_N003` | DEC | 10 | $0.78 | HE7-22 | 4 | **OPEN** | **false** |

Every settlement column (`settled_at`, `settle_da`, `settle_fmm`,
`pnl_per_mwh`, `pnl_dollars`, `filled_hours`) is null on both, and
`unsettled_reason` is null on both — hence OPEN, not PENDING.

**NOTHING HAS SETTLED YET.** So today the equity curve is honestly `points: []`,
the book's settled block is zeros with `win_rate: null`, and by-node returns two
nodes with live exposure and no record. The settled branches are real, tested
against hand-built rows, and light themselves the day the settlement writer
runs — no code change here. This is the same posture the Regime lane took with
its percentile branch at n = 6…8.

**Read this before wiring a caption:** every bid banked to date carries
`frontier_ok = false`, i.e. the position was taken on inputs the writer's own
freshness check flagged (the DAM constraints stamp did not predate the run).
That is why `frontier_ok` ships on the blotter row even though it was not in the
requested field list — a blotter that hides it lies by omission. Two other
additive fields: `entry_id` (a stable row key the client needs) and
`constraint_id`.

---

## 3. THE FINDING — there is no machine-readable play key

The by-play stanza was specified as "keyed off the screen named in
`rationale`/`inputs_as_of` — read what's there". I read what's there. **It is
not there in machine-readable form**, so the stanza serves `play: null` with the
reason attached, exactly as instructed, rather than regexing prose.

What `inputs_as_of` actually holds on a `kind='bid'` row:

| path | value | why it is not a play key |
|---|---|---|
| `map.map_id` | `"desk_reader_prebid"` | identical on **every row in the table**, notes included. That is the agent program, not the screen — grouping on it yields one bucket mislabelled as a play |
| `map.version`, `map.scout_maps_id` | `"v1"`, `9` | same: program identity |
| `bid.{size_mw,pnode_id,direction,conviction,hour_scope,price_limit,constraint_id}` | position fields | describe the position, not the screen that fired it |
| `bid.{rules,fingerprint,persistence,settle_reference,conviction_reasons}` | metrics blocks | see the near miss below |
| `bid_screen.facts[]` | free prose (`"GATE 1 OK: …"`, `"CANDIDATE …"`, `"PRICED …"`, `"DROP (duplicate position) …"`) | English sentences |
| `bid_screen.{positions[],bids_filed}` | the run's output | run-level, not per-bid |

The screen name appears in exactly one place: the `rationale` column's prose,
under the heading `WHICH SCREEN FIRED`, e.g.

```
WHICH SCREEN FIRED
  Persistence set — bound on 7 of the window's days (146.0 DAM hours …), the 7-of-7 tier.
  No phantom/surprise corroboration this run — the position rests on persistence
  plus fingerprint agreement alone.
```

Keying an aggregate off that makes the aggregate a property of the writer's
sentence style. Not built.

### The near miss, named so nobody re-discovers it

`inputs_as_of.bid.persistence` **does** exist as a metrics block
(`{dam_hours_7d: 146.0, days_present: 7}`), and both banked bids **did** fire off
the persistence screen. Inferring `play = "persistence"` from the *presence* of
that key is schema-shape divination, not a contract:

1. There is no documented phantom or surprise counterpart to discriminate
   against — nothing says a phantom-fired bid would carry `bid.phantom`.
2. Both banked rows come from a **single run on a single trade date**, so the
   guess cannot even be checked against a counterexample.

A test (`test_the_presence_of_bid_persistence_is_not_treated_as_a_play`) pins
that it is not used.

### The writer fix — one field, no migration of existing rows

Stamp on each bid:

```json
"inputs_as_of": { "bid": { "screen": "persistence" } }
```

with `screen ∈ {"persistence", "phantom", "surprise"}`.

**The lookup here is already live.** `paper_desk.PLAY_KEY_PATHS` searches
`bid.screen`, `bid.play`, `screen`, `play` in that order, and the endpoint
publishes the list it searched as `key.paths_searched`. The day the writer
stamps the field, `/api/desk/by-play` groups on it **with no change to this
repo** — pinned by
`test_the_play_lookup_is_live_and_groups_the_day_the_writer_stamps_it`.

**One open question for the writer.** The rationale reads "No phantom/surprise
corroboration this run", which implies a bid *can* be corroborated by more than
one screen. If that is the intent, `bid.screens: ["persistence", "phantom"]` is
the right shape and v1 should group on the array. **v0 reads scalars only** and
reports an array as present-but-not-groupable rather than inventing a
flattening rule the writer never agreed to
(`test_a_multi_screen_array_is_reported_not_silently_flattened`).

---

## 4. The five rails, and where each is pinned

Stated once in `paper_desk.py`'s docstring, tested individually:

1. **Notes are not bids.** Filtered in the SQL predicate *and* re-asserted in
   `bid_rows`, so a widened query cannot leak prose into a position count.
2. **Status is derived server-side, from two columns.** `settled` is terminal
   and checked first, so a row carrying both `settled = true` and a stale
   `unsettled_reason` reads SETTLED — and the reason still ships, so the
   contradiction is visible rather than swallowed. A whitespace-only reason is
   absence, not PENDING (an amber chip with no text is worse than no chip).
3. **A position marks only at settlement.** `settled` is the sole P&L gate. An
   OPEN or PENDING row carrying a stamped `pnl_dollars` contributes **nothing**
   — this is the test that catches someone wiring a running mark into v0.
4. **P&L is read, never recomputed.** A row whose stored `pnl_dollars`
   contradicts `(settle_fmm − settle_da) × MWh` serves the **stored** figure;
   dropping both price columns to null must change no total anywhere
   (asserted as byte-equality across all three derived stanzas).
5. **Empty is `[]`, thin is `null`.** Per the isOutlooks doctrine. A `0.0` win
   rate reads "everything lost"; `null` reads "nothing has settled".

Plus two accounting rules:

- **Zero-fill is FLAT, never a loss** — whether the writer stamps `0.00` or
  leaves the column null on a `filled_hours = 0` settlement.
- **Nothing is quietly dropped**: `win + loss + flat + unclassified ==
  settled.n` is asserted as an invariant. A settled row with a real fill and no
  P&L is `unclassified` — a hole in the ledger, surfaced rather than laundered
  into flat to make a win rate look clean.

**Rounding, and why the totals tie.** Row money is rounded to the cent and row
$/MWh to 4dp *as the row is normalized*, and every aggregate sums those rounded
row values. So the book's cumulative dollars is exactly the sum of the dollars
the blotter displays, and the curve's last point is exactly the book's total.
Summing raw and rounding late is marginally more precise and lets a desk add up
the visible column and get a different answer.

**Zero LLM, grep-enforced.** `_BANNED_LLM_VOCABULARY` is greped against
`inspect.getsource(paper_desk)` **and** against all five route functions plus
`_pd_read_bids` / `_pd_base`, in the house pattern from
`test_structures.py::test_no_forward_pricing_vocabulary_anywhere_in_the_module`.
A third test asserts the pure layer imports nothing but `datetime`.

---

## 5. EXPLAIN receipt — the lane's one query shape

`EXPLAIN (ANALYZE, BUFFERS)` on Neon `Energylake`, 2026-07-31:

```
Sort  (cost=3.21..3.21 rows=1 width=418) (actual time=0.033..0.033 rows=2)
  Sort Key: trade_date, entry_id
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=6
  ->  Seq Scan on paper_journal
        (cost=0.00..3.20 rows=1 width=418) (actual time=0.016..0.016 rows=2)
        Filter: (kind = 'bid'::text)
        Rows Removed by Filter: 17
        Buffers: shared hit=3
Planning Time: 2.367 ms
Execution Time: 0.055 ms
```

**A Seq Scan is the right plan here and is not a finding.** The whole table is
three pages, so any index would cost a second read to save nothing. The table
does carry `uniq_paper_journal_bid_position (trade_date, pnode_id, direction)
WHERE kind = 'bid'` — a write-side uniqueness guard, not a read path; this lane
neither needs nor uses it. Worth revisiting when the journal reaches the low
thousands of rows; at 19 it is 0.055 ms and 3 buffers.

All five stanzas ride this one shape — **one read per request**, asserted by
`test_one_read_per_request_and_only_the_lanes_one_query_shape`. `inputs_as_of`
is selected (the play lookup needs it) and **never** reaches the wire: it is a
heavy jsonb blob carrying the full bid screen with its prose facts, and the
blotter is a position list, not an audit log.

---

## 6. Verification receipt

Outbound `:5432` is blocked by this container's network policy, so the app could
not dial the pantry directly. The stronger available receipt was taken instead:
**the exact 19 rows read out of Neon were replayed through the real route
handlers** — real SQL param binding, real route functions, real
`paper_desk.py`; only the psycopg wire stubbed. Result:

| stanza | status | payload |
|---|---|---|
| `/api/desk/blotter` | 200 | `n: 2`, `by_status {OPEN: 2, PENDING: 0, SETTLED: 0}`, 17 notes excluded |
| `/api/desk/book` | 200 | `open {n: 2, gross_mw: 20.0}`, `settled.n: 0`, `win_rate: null`, `pnl_dollars: 0.0` |
| `/api/desk/equity` | 200 | `points: []`, `span: null` |
| `/api/desk/by-node` | 200 | 2 nodes, each `open_gross_mw: 10.0`, `win_rate: null` |
| `/api/desk/by-play` | 200 | one `play: null` group, `n: 2`, `key.available: false` |

Reads issued across all five: **5** (one per request), params `{"kind": "bid"}`
every time.

Those figures are also frozen as the `test_acceptance_*` tests, so a regression
against today's banked reality fails the suite rather than drifting silently.

**One coverage gap the receipt exposed and closed:** the first pass of the tests
built rows with Python `int`/`float`, but psycopg returns `numeric` as
`Decimal`. `test_numeric_columns_arrive_as_decimal_and_survive_the_wire` now
drives every money/rate column as `Decimal` through the full route.

---

## 7. V1 DEBT, FILED NOT BUILT

**Banked-hours running mark.** v0 marks a position only at settlement, so an
OPEN bid filed on 2026-07-30 for HE7-22 shows no P&L even once some of those
hours have printed in the lake. A v1 could mark the *banked* hours of an open
position against realized DA/FMM prints and serve a running, explicitly-partial
mark.

Filed here, deliberately not built, because it is a different animal from this
lane: it needs a price read against `atlas_pnode_lmp_snapshot` (a second query
shape, per-node, per-HE), a partial-hours convention (does a 6-of-16-hour mark
scale to full size or stay at 6 hours' worth?), and a vocabulary that can never
be confused with a settled figure on the same page. Wiring it into v0's
`pnl_dollars` would silently mix marked and settled dollars in one column, which
is exactly the failure `test_an_unsettled_row_with_a_stamped_pnl_books_nothing`
now guards against.

If it is built, it belongs in a **separate, separately-named block**
(`mark: {...}` alongside, never inside, the settled figures), and the equity
curve should stay settled-only.

**Second, smaller:** the five stanzas each issue their own read, so a client
rendering all five could in principle mix two instants. At 19 rows and a
once-daily writer this is not a live risk, but if the desk page grows a
"refresh all" it may be worth a single `/api/desk/package` that reads once and
returns all five under one `as_of`.

---

## 8. What a consumer must not do

- **Do not recompute P&L from `settle_da` / `settle_fmm`.** They are display
  columns. The signed truth is `pnl_dollars` / `pnl_per_mwh`.
- **Do not read a gap in the equity curve as a flat day.** Consecutive points
  are not necessarily adjacent dates. Draw the gap.
- **Do not treat `win_rate: null` as 0.** Null is "nothing has settled".
- **Do not parse `unsettled_reason`.** Render it.
- **Do not treat a 200 with `rows: []` as "the DB is down".** It is not — a DB
  failure is a 503 with a plain body, never a fabricated empty desk.
