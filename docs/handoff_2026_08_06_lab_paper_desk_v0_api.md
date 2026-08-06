# Handoff — The Lab Paper Desk v0 (`energylake-api`)

**Date:** 2026-08-06
**Branch:** `claude/paper-desk-api-endpoint-wnmom4`, branched off `main` at
`92a12cc` (after the hub-attribution fix, PR #59, merged)
**House law followed:** `docs/handoff_2026_07_31_paper_desk_v0_api.md` and the
Regime lane before it — pure-layer/route split, fake-pool tests, honest
empty/thin states, EXPLAIN receipt per new query shape, zero-LLM grep.

**Scope shipped:** one endpoint, `GET /api/lab/paper-desk`, + 73 new tests.
Suite: **1118 passed** (73 new, 1045 pre-existing, **none modified**).
**No migrations. No new tables. No new indexes. No writes. No existing endpoint
changed.** One new query shape, EXPLAIN'd below.

New files: `paper_lab.py` (the pure layer) and `tests/test_paper_lab.py`.
`main.py` gains one route, one SQL constant, one reader and a route-index line;
`README.md` gains a Lab Paper Desk section.

---

## 1. The response contract, as served

Pinned verbatim from the brief. Lane B builds against this.

```
{
  "as_of": iso,
  "blotter": [ { entry_id, trade_date, pnode_id, author, direction, size_mw,
      price_limit, hour_scope, status, unsettled_reason, settle_da, settle_fmm,
      pnl_per_mwh, pnl_dollars, settled_at, settled_by_version, rationale,
      inputs_as_of, curve_points } ],
  "equity":  [ { date, cumulative_pnl } ],
  "by_node": [ { pnode_id, n_settled, n_pending, wins, total_pnl,
                 avg_pnl_per_mwh } ],
  "by_play": [ { play, n_settled, wins, total_pnl } ],
  "book":    { settled_pnl, n_settled, n_pending, n_void }
}
```

Every required key above is asserted as an **exact set** by
`test_the_top_level_contract_is_exactly_what_was_pinned` and its three
siblings, so a rename or a quiet drop fails the build here rather than blanking
a column in someone's UI. The blotter's key **order** is pinned too, so a diff
of the response against the brief reads cleanly.

### Additive fields, and why each one earns its place

Four, all documented in the module as data (`BLOTTER_ADDITIVE_KEYS`,
`BY_NODE_ADDITIVE_KEYS`, `BY_PLAY_ADDITIVE_KEYS`) and asserted to be *exactly*
these — nothing reaches the wire unannounced.

| field | where | why |
|---|---|---|
| `play` | blotter row | `by_play` groups on it. A client colouring a blotter row by play would otherwise re-derive it from `inputs_as_of` — a second implementation of the same rule, waiting to drift. |
| `n_void` | `by_node`, `by_play` | **Load-bearing.** Without it, `LUNDY_7_N003` — 7 bids, every one voided — renders as a row of zeroes indistinguishable from a node that never traded. |
| `n_pending` | `by_play` | So the two aggregate tables read the same way. A play with 1 settled and 5 pending bids is not "1 bid". |
| `derivation`, `source`, `paper_only` | top level | Every rule the endpoint applied, stated where the payload is served, plus the house's source/paper stamps. |

---

## 2. Depth report, measured 2026-08-06

`paper_journal`, full table — **it has grown since the 2026-07-31 handoff**:

| kind | rows (07-31 → 08-06) | span (trade_date) |
|---|---|---|
| `note` | 17 → **24** | 2026-07-12 … 2026-08-06 |
| `bid` | 2 → **14** | 2026-07-30 … 2026-08-05 |
| **total** | 19 → **38** | |

The 14 bids, by this lane's derived status:

| status | n | which |
|---|---|---|
| `settled` | **1** | 22 — `CONTROLX_1_N003`, −$918.93, settled 2026-08-06T21:14:11Z by settler v2 |
| `pending` | **5** | 25, 28, 31, 34, 37 — all `CONTROLX_1_N003`, all `awaiting_settlement` |
| `void` | **8** | 18, 19 (`voided_settler_v1_double_booked_position`) + 21, 24, 27, 30, 33, 36 (`voided_writer_alias_double_booked`) |

Every bid carries exactly **16** rows in `paper_bid_curves` (HE7-22, one
segment per hour); 224 curve rows total across 14 entries.

`unsettled_reason` carries exactly three distinct values across the table
(`voided_writer_alias_double_booked` ×6, `awaiting_settlement` ×5,
`voided_settler_v1_double_booked_position` ×2, plus null on the settled row),
so the `voided` prefix separates voids from pending cleanly.

---

## 3. THE FINDINGS

### 3.1 The play key is LIVE — the predicted finding has been overtaken

The brief anticipated serving `play = "unclassified"` and booking the finding,
"the Trading Room capture predicted exactly this". The prediction was correct
about the 2026-07-31 state and **has since been overtaken by the writer.**

`/api/desk/by-play` shipped a live-but-dark lookup on 2026-07-31 and filed a
one-field fix: stamp `inputs_as_of.bid.screen ∈ {persistence, phantom,
surprise}`. **The writer took it.** Re-measured against all 14 bids:

| rows | `inputs_as_of.bid.screen` |
|---|---|
| 21, 24, 27, 30, 33, 36 | `"surprise"` (jsonb type `string`) |
| 22, 25, 28, 31, 34, 37 | `"persistence"` (jsonb type `string`) |
| **18, 19** | **absent** |

So this lane groups on the real key for **12 of 14** rows. Entries 18 and 19
were filed 2026-07-30T12:47:48Z, *before* the writer stamped the field, and
both are voided — they read `"unclassified"`, honestly. **They are not
backfilled and no play is guessed for them**: inventing a play for a row filed
under a writer that did not carry the field is a guess dressed as a record.

**Corroboration, not dependence.** On all 12 stamped rows the rationale prose
agrees with the key exactly — the `screen <name> — the screen that set the
side` line matches `bid.screen`, **12 for 12**. That is a receipt that the
writer's two surfaces are consistent. It is **not** a fallback: this lane does
not read the rationale to derive a play, then or now, because an aggregate
keyed off prose is an aggregate that changes when the writer edits a sentence.
`test_the_rationale_prose_is_never_parsed_to_key_a_play` pins that a row with
the prose and no stamped key is still `unclassified`.

**One implementation.** `paper_lab.play_of` delegates to `paper_desk.play_of`
rather than re-implementing the lookup; only the *absent* case differs (null
there, `"unclassified"` here — each lane's own contract).
`test_the_play_lookup_is_not_re_implemented_in_this_lane` asserts the searched
paths are literally `paper_desk.PLAY_KEY_PATHS`, so a path added there is
picked up here with no change.

**Why `/api/desk/by-play` still serves `null`** and was not "fixed" in this
branch: its `key.available` contract distinguishes full coverage from partial,
12 of 14 is partial, and changing it would move a live endpoint under a client
for no gain. Both lanes are correct about their own contract.

### 3.2 The desk's void rate is 57%, and `/api/desk/*` cannot show it

**8 of 14 paper positions were voided as double-books.** `/api/desk/*` has no
void concept — a voided row *is* an unsettled row carrying a reason, so it
reads `PENDING` there. That was invisible on 2026-07-31 because nothing had
been voided; it is not invisible now, and a page that files 8 dead positions
under "pending" tells the desk it is waiting on settlements that are never
coming. **This is the strongest argument for the endpoint existing**, and it
is why voids ride the blotter here rather than being filtered out.

Two sub-findings worth the writer's attention, filed not fixed — this lane
reads, it does not adjudicate:

1. **Every bid ever filed at `LUNDY_7_N003` is void.** All 7, six of them under
   `voided_writer_alias_double_booked` and one under the settler's v1 reason.
   The desk has a node it has traded seven times and booked nothing from, ever.
   `by_node` keeps it visible with `n_void: 7` precisely so this is legible.
2. **Two different void reasons for what reads like one defect.** The settler
   coined `voided_settler_v1_double_booked_position` and the writer coined
   `voided_writer_alias_double_booked`. Both are served verbatim and neither is
   mapped to a code here. If they are the same defect seen from two sides, the
   desk will want one vocabulary; if they are not, the distinction is real and
   should be documented. **Not this lane's call.**

### 3.3 `settled_by_version` is stamped on rows that have not settled

Entries 25, 28, 31, 34 and 37 carry `settled_by_version = 2` while
`settled = false` and `unsettled_reason = 'awaiting_settlement'`. The column is
served verbatim per the contract and nothing here interprets it. Flagged
because a consumer reading `settled_by_version` as "this settled, by version N"
will be wrong on 5 of 14 rows — it appears to mean "the settler that last
*looked* at this row", not "the settler that settled it". Worth the writer
either renaming or documenting.

---

## 4. The rails, and where each is pinned

Stated once in `paper_lab.py`'s docstring, tested individually:

1. **Status is three-valued, derived server-side, and `settled` is terminal.**
   Checked first, so a row carrying both `settled = true` and a `voided_*`
   reason reads `settled` — and the reason still ships, so the contradiction is
   visible on the page rather than resolved out of sight. A whitespace-only
   reason is absence, not a void.
2. **There is no `open` status.** `/api/desk/*` splits unsettled-with-no-reason
   (`OPEN`) from unsettled-with-a-reason (`PENDING`); this contract does not,
   and both land on `pending`. A contract choice, pinned so neither
   vocabulary leaks into the other.
3. **Voids are excluded from every aggregate figure and counted in the book.**
   The exclusion happens in exactly one function (`_stats`), so it is
   impossible to forget in one aggregate and remember in another. A voided row
   carrying a stamped P&L contributes nothing anywhere — asserted as
   byte-equality of all four derived stanzas against the same rows with the P&L
   columns dropped.
4. **A void-only group stays visible**, with zeroes and its `n_void`. Dropping
   it would take the voids off the page, which is the failure this endpoint
   exists to prevent.
5. **A position marks only at settlement.** No running mark. A pending or void
   row with a stamped P&L books nothing.
6. **P&L is read, never recomputed.** `settle_da` / `settle_fmm` are on the
   wire in this lane, right beside `pnl_dollars`, which makes the temptation
   worse — so the test that a contradictory stored figure wins is carried here
   too.
7. **Empty is `[]`, thin is `null`.** With one deliberate exception:
   `total_pnl` is a **sum**, and the sum of no settled rows is `0.0`, which is
   the arithmetic truth and stays addable across groups. `n_settled` sits
   beside it. Averages are `null`, never `0.0`.

Plus two accounting invariants, both asserted:

- `n_settled + n_pending + n_void == len(blotter)`, always.
- `book.settled_pnl` == the sum of the blotter's **visible** `pnl_dollars`
  column == the curve's last `cumulative_pnl`. Row money is rounded to the cent
  as the row is normalized and every aggregate sums those rounded values, so a
  desk that adds up the visible column gets the same answer.

### The curve is by `settled_at`, and that is a real difference

`/api/desk/equity` draws by `trade_date` — when the position was taken. This
draws by **when the money was booked**. The two will not agree and must not be
expected to: the one settled bid was taken 2026-07-31 and settled 2026-08-06,
so `/api/desk/equity` puts it on 07-31 and this puts it on 08-06.

`settled_at` is **normalized to UTC on the wire** (`paper_lab._iso_utc`).
Load-bearing: psycopg renders `timestamptz` in the connection's session
timezone, and the curve buckets on that string's calendar date — leaving the
offset to chance would let a settlement drift a day between deployments.
A settled row with no `settled_at` cannot be placed on a curve at all; it is
excluded and **counted** in `derivation.equity.settled_rows_without_settled_at`,
never parked on a fabricated date and never dropped in silence.

---

## 5. EXPLAIN receipt — the lane's one query shape

`EXPLAIN (ANALYZE, BUFFERS)` on Neon `Energylake`, 2026-08-06:

```
Sort  (cost=74.63..74.66 rows=14 width=1088) (actual time=0.222..0.223 rows=14)
  Sort Key: j.trade_date DESC, j.entry_id DESC
  Sort Method: quicksort  Memory: 27kB
  Buffers: shared hit=34
  ->  Seq Scan on paper_journal j
        (cost=0.00..74.36 rows=14 width=1088) (actual time=0.035..0.208 rows=14)
        Filter: (kind = 'bid'::text)
        Rows Removed by Filter: 24
        SubPlan 1
          ->  Aggregate  (cost=4.84..4.85 rows=1) (actual time=0.013..0.013 loops=14)
                ->  Seq Scan on paper_bid_curves c
                      (cost=0.00..4.80 rows=16) (actual rows=16 loops=14)
                      Filter: (entry_id = j.entry_id)
                      Rows Removed by Filter: 208
Planning Time: 0.190 ms
Execution Time: 0.256 ms
```

**Two Seq Scans, and both are the right plan — not a finding.** Both tables are
a handful of pages (38 journal rows, 224 curve rows), so an index would cost a
second read to save nothing; the planner is choosing correctly.
`paper_bid_curves` already carries the index for the day it matters —
`paper_bid_curves_pkey (entry_id, he, segment)`, leading on `entry_id` — so the
correlated subplan flips to an index scan on its own as the table grows, with
no change to this repo.

The curve count is a **correlated subquery, not a second round trip**, so the
endpoint keeps its one-read promise: a client cannot see a blotter row whose
curve count came from a different instant than the row itself.
`test_one_read_per_request_and_the_curve_count_rides_it` pins both halves.

**The thing to watch** is the subplan's `loops=14` — it is O(bids). At 14 bids
it is 0.256 ms total. At a few thousand it wants the index scan the PK will
provide; at tens of thousands this endpoint wants pagination, and so does the
payload (below).

**Payload weight.** `rationale` and `inputs_as_of` are on the wire here, a
deliberate difference from `/api/desk/blotter`, which withholds them as heavy
columns. This page is an audit surface — the point is that a reader can see
what a position was taken on. Measured 2026-08-06: 36 kB of jsonb and ~33 kB of
prose across all 14 bids, and the **full serialized response is 13.3 kB**. Worth
re-measuring, and probably worth a `?slim=1` or pagination, when the journal
reaches the low thousands of rows.

---

## 6. Verification receipt

Outbound `:5432` is blocked by this container's network policy, so the app
could not dial the pantry directly. The stronger available receipt was taken
instead: **the 14 bids read out of Neon were replayed through the real route
handler** — real SQL param binding, real route function, real `paper_lab.py`;
only the psycopg wire stubbed.

```
HTTP 200 · reads issued: 1 · params: {"kind": "bid"} · payload 13,277 bytes
blotter n: 14   entry order: 37,36,34,33,31,30,28,27,25,24,22,21,19,18
statuses : {"settled": 1, "pending": 5, "void": 8}
curve_pts: all 16
book     : {"settled_pnl": -918.93, "n_settled": 1, "n_pending": 5, "n_void": 8}
equity   : [{"date": "2026-08-06", "cumulative_pnl": -918.93}]
by_node  : CONTROLX_1_N003  n_settled 1  n_pending 5  wins 0
                            total_pnl -918.93  avg -11.4867  n_void 1
           LUNDY_7_N003     n_settled 0  n_pending 0  wins 0
                            total_pnl 0.0  avg null      n_void 7
by_play  : persistence    n_settled 1  wins 0  total_pnl -918.93  n_pending 5  n_void 0
           surprise       n_settled 0  wins 0  total_pnl 0.0      n_pending 0  n_void 6
           unclassified   n_settled 0  wins 0  total_pnl 0.0      n_pending 0  n_void 2
play key : keyed 12 / unclassified 2 / available false
entry 22 : settled · trade_date 2026-07-31 · settled_at 2026-08-06T21:14:11.443000+00:00
           settled_by_version 2 · unsettled_reason null · settle_da -50.3591
           settle_fmm -61.8458 · pnl_per_mwh -11.4867 · pnl_dollars -918.93
           play "persistence" · curve_points 16
```

Those figures are frozen as the `test_acceptance_*` tests, so a regression
against today's banked reality fails the suite rather than drifting silently.

**What the acceptance fixture pins verbatim, and what it represents.** Every
scalar column is the value Neon returned, byte for byte, driven as `Decimal`
where the column is `numeric` (psycopg does not hand back floats). The two
heavy columns are represented by what this lane actually reads out of them —
`inputs_as_of` by its real `bid.screen` stamp (present on 12, absent on 18/19)
and `rationale` by its real first line — rather than by 69 kB of embedded blob.
Their byte-for-byte passthrough is pinned separately and structurally: the lane
performs no transformation on either column, and
`test_the_rationale_and_the_inputs_blob_reach_the_wire_unreshaped` asserts
identity.

---

## 7. V1 DEBT, FILED NOT BUILT

**Pagination and a slim mode.** The payload is one unbounded list. At 14 bids
that is 13.3 kB; the growth is linear in bids and dominated by `rationale` +
`inputs_as_of`. When the journal reaches the low thousands this endpoint wants
`?limit`/`?since` and probably a `?slim=1` that drops the two heavy columns.
Not built at n = 14, because a pagination contract invented before anyone has
hit the wall is a contract that will be wrong.

**The two lanes are not merged, and neither is deprecated.** `/api/desk/*` is
`OPEN`/`PENDING`/`SETTLED` with a `trade_date` curve; this is
`settled`/`pending`/`void` with a `settled_at` curve. Both have consumers, and
collapsing them would force one of the two to change under a client. If the
desk decides the void vocabulary is the right one everywhere, that is a
deliberate migration with a deprecation window — not a refactor.

**A void reason taxonomy.** See §3.2.2 — two reasons for what may be one
defect. This lane serves both verbatim and maps neither. If the desk wants
`by_void_reason`, the data supports it today; the vocabulary question has to be
settled by the writer first.

---

## 8. What a consumer must not do

- **Do not treat a void as pending.** It is not waiting on anything. 8 of 14
  banked bids are voids; a "pending" chip on them is a lie about the desk's
  exposure.
- **Do not drop a void-only node or play from a rendering** because its numbers
  are zero. `n_void` is why it is there.
- **Do not recompute P&L from `settle_da` / `settle_fmm`.** They are display
  columns. The signed truth is `pnl_dollars` / `pnl_per_mwh`.
- **Do not expect this curve to match `/api/desk/equity`.** One is by
  `settled_at`, the other by `trade_date`. Both are right.
- **Do not read a gap in the equity curve as a flat day.** Consecutive points
  are not necessarily adjacent dates. Draw the gap.
- **Do not re-derive `play` from `inputs_as_of` in the client.** It is served.
  Two implementations of one rule is one implementation too many.
- **Do not read `settled_by_version` as "this settled".** See §3.3 — it is
  stamped on 5 unsettled rows.
- **Do not treat a 200 with empty lists as "the DB is down".** It is not — a DB
  failure is a 503 with a plain body, never a fabricated empty desk.
