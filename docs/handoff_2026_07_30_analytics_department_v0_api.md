# Handoff — The Analytics Department v0, Part 1 (`energylake-api`)

**Date:** 2026-07-30
**Spec:** `cc_spec_2026_07_30_analytics_department_v0.md`
**Branch:** `claude/new-session-mei5np`
**Scope shipped:** all three api endpoints + 100 contract tests. Part 2
(`energylake-dashboard`) is NOT in this PR, per the spec's api-first ordering.

Suite: **659 passed** (100 new, 559 pre-existing, none touched).

---

## 1. Depth report (spec's first act; acceptance 1)

`atlas_pnode_lmp_snapshot`, sentinel-floored (`market_date >= 2020-01-01`),
measured 2026-07-30:

| market | min_date | max_date | dates | complete days | rows | nodes |
|---|---|---|---|---|---|---|
| DAM  | 2026-07-22 | 2026-07-29 | 8 | 6 | 2,491,088 | 14,827 |
| RTPD | 2026-07-22 | 2026-07-29 | 8 | 6 | 9,756,166 | 14,827 |

Per-date detail (the partial edges matter downstream):

| date | DAM rows | DAM HE span | RTPD rows | RTPD intervals |
|---|---|---|---|---|
| 07-22 | 44,481 | HE22–24 | 207,578 | 14 |
| 07-23 | 355,848 | HE1–24 | 1,364,084 | 92 |
| 07-24 | 355,848 | HE1–24 | 1,349,257 | 91 |
| 07-25 | 355,848 | HE1–24 | 1,408,565 | 95 |
| 07-26 | 355,848 | HE1–24 | 1,364,084 | 92 |
| 07-27 | 355,848 | HE1–24 | 1,423,392 | 96 |
| 07-28 | 355,848 | HE1–24 | 1,423,392 | 96 |
| 07-29 | 311,367 | HE1–21 | 1,215,814 | 82 |

RTD spans the same window and additionally carries the known
`market_date = 0001-01-01` sentinel rows. This lane never reads RTD (house
axiom: RTD is display context, never a settlement leg).

### STOP-gate 3 is TRIPPED, and it changes what v0 can mean

The spec's own example caption said *"distribution over 21 days banked."*
**It is eight dates, six of them complete.** The hot tier is ~7-day retention
with a dispatch-only writer that has no standing schedule. Consequences, stated
rather than papered over:

1. **The monthly ledger has exactly ONE row** — `2026-07`, `complete: false`,
   112 7x16 hours out of a 31-day month. There are **no prior full months
   within depth**. The ledger code generalizes to many months and to
   MTD-vs-full; the data does not yet. The spec's line about "prior full months"
   cannot be satisfied by any implementation against this table today.
2. **Every per-HE envelope rests on N=6–8.** p5/p95 on seven samples is very
   nearly min/max. The bands are real and each carries its own `n`, but they are
   **not a climatology**, and the dashboard caption must say "8 days banked",
   not "21 days".
3. Per the STOP-gate's instruction ("state it, proceed with honest N"), I
   proceeded. Nothing in the payloads implies history the pantry does not hold.

**Captain decision needed:** if the department is meant to show a real
distribution, something has to bank nodal history beyond the 7-day tier. That is
a pantry-side change and outside this spec — flagging it, not doing it.

---

## 2. STOP-gate 1 — hub pairing: RESOLVED, attribute found

The spec proposed *"geographic zone membership NP15/SP15/ZP26 via the
universe/gazetteer attributes."* **No such attribute exists.** What I checked:

- `atlas_pnode_lmp_snapshot.area` / `atlas_pnode_geo_corrected.area` are
  **balancing authorities** (CA, BPAT, APS, PACE, NV, SRP, …), not LAP zones.
- No table in the pantry has a `zone`/`lap` column (checked all 88 tables).
- Coordinates exist (14,417 of 14,827 priced nodes) but there is no zone polygon
  to test them against — inferring NP15/SP15/ZP26 from latitude would be exactly
  the guessed mapping the gate forbids.

**What does exist: `atlas_pnode_universe.th_hub` — CAISO's own published
node→trading-hub attribution.** That is the pairing, used verbatim:

```
TH_NP15_GEN-APND -> NP15      TH_PACE_GEN-APND -> paired, no banked hub series
TH_SP15_GEN-APND -> SP15      TH_PACW_GEN-APND -> paired, no banked hub series
TH_ZP26_GEN-APND -> ZP26
```

Pinned in tests as `pairing_rule: "caiso_th_hub_v0"`, including a test that
fails if the function ever grows a `latitude`/`longitude`/`area` branch.

### Coverage — read this before wiring the basis panel

| slice | paired / total | % |
|---|---|---|
| all priced nodes | 1,579 / 14,827 | 10.7% |
| CA-area nodes | 1,579 / 4,252 | 37.1% |
| **CA-area GEN nodes** | **1,487 / 1,860** | **79.9%** |
| LOAD nodes | 92 / 10,943 | 0.8% |

**Basis is a generator story.** Most load nodes and essentially every non-CAISO
WEIM node have no CAISO trading hub — which is *correct*, not a gap to fill. For
those, `basis` and `ledger` return `available: false` plus a reason. That is the
**common path** (~89% of the universe), not an edge case, so the room must render
absence gracefully rather than treating it as an error state.

Three verdicts are distinguished, never collapsed: `paired`,
`paired_unpriceable` (CAISO pairs it, pantry has no series — 218 nodes), and
`unpaired`.

### One spec fact confirmed, one corrected

- ✅ **"TH hub nodes are NOT in this table"** — confirmed. No `TH_*_GEN-APND`
  rows appear in the priced universe. (`TH_LNODE*` rows exist but are APS load
  nodes, not hubs — worth knowing before writing a `LIKE 'TH%'` filter.)
- ⚠️ **"The 42GB table"** is `timeseries_values` (43 GB).
  `atlas_pnode_lmp_snapshot` is 6.8 GB / ~23M rows. Both handled under the same
  discipline; noting it so the number isn't repeated onto the wrong table.

---

## 3. STOP-gate 2 — movers: index-served, runtime measured

Not tripped, but it took work, and the result is **two different chunk shapes**
because one shape for both markets costs an order of magnitude.

**The governing fact:** on this compute (Neon 0.25–2 CU, pg17) the planner
abandons `idx_lmp_snap_market_time` for any predicate matching more than roughly
5% of the snapshot table and parallel-seq-scans instead. Every row below is
`EXPLAIN (ANALYZE)` on real data:

| shape | rows | plan | time |
|---|---|---|---|
| `market=DAM` + 1 date | 311k | bitmap index | 156 ms |
| `market=DAM` + 7 dates | 2.2M | **SEQ SCAN** | 2,231 ms |
| `market=RTPD` + 1 date | 1.4M | **SEQ SCAN** | 1,387 ms |
| `market=RTPD` + 7 dates, `GROUP BY (node,d,he)` | 9.5M | **SEQ SCAN + 430 MB spill** | **38 s** |
| `DISTINCT (d,he,iv)` over RTPD 7 dates | 9.5M | **SEQ SCAN** | 18,071 ms |
| `market=RTPD` + 1 date + 1 hour | 59k | bitmap index | 62 ms |
| **`market=RTPD` + 1 date + 6 hours (VALUES join)** | **356k** | **bitmap index** | **226 ms** |
| `market=RTPD` + 1 date + 24 hours (VALUES join) | 1.4M | **SEQ SCAN** | 1,818 ms |
| `market=DAM` + 1 date + 6 hours (VALUES join) | 89k | **plain INDEX SCAN** | 2,430 ms |
| **`market=DAM` + 1 date + `ANY(24 hours)`** | **356k** | **parallel bitmap heap** | **1,254 ms** |

**Pinned shapes:**
- **RTPD** — 6-hour chunks, inline `VALUES (he, 1/k)` grid **plus** a redundant
  `market_hour BETWEEN lo AND hi` bound. Both halves are load-bearing: the
  VALUES grid puts the tiny relation on the outer side of a nested loop (one
  bitmap index scan per hour) and the BETWEEN bound keeps the estimate under the
  cliff. `HashAggregate` on `pnode_id` only — no sort, no spill.
- **DAM** — one chunk per date, `market_hour = ANY(hours)`. Reusing the RTPD
  shape here collapses to a plain Index Scan: 2,430 ms for a *quarter* of the
  rows.

**The weighted single-pass trick** (why there is no 430 MB spill): the exact
statistic is the mean over matched hours of `hourly-mean RTPD − DA`, which
naively needs `GROUP BY (pnode_id, market_date, market_hour)` over 9.5M rows.
Avoidable because **RTPD interval coverage is node-independent** — the writer
pulls the whole universe per dispatch, so a ragged hour is ragged for every node
at once. Verified: 2026-07-23 HE17 carries 3 intervals × 14,827 nodes = 44,481
rows exactly; HE24 carries 2 × 14,827 = 29,654. So `k(d,he)` is a scalar grid
read off ONE reference node in ~7 ms, and
`mean_h RTavg(h) = Σ lmp_i / (H · k(d,he))` becomes a single hash aggregate.

### Measured runtime (acceptance 4)

Per-chunk server-side execution times, cold and warm (a 0.25–2 CU autoscaling
compute genuinely swings this much):

| chunk | cold | warm |
|---|---|---|
| RTPD 6h (356k rows) | ~1.3 s | 0.23–0.42 s |
| DAM 1 date (356k rows) | ~1.25 s | ~0.3 s |

| request | reads | sequential | at concurrency 4 |
|---|---|---|---|
| `dart` 1d | 5 | ~6.5 s cold / ~1.5 s warm | **~2.0 s cold / ~0.5 s warm** |
| `dart` 7d | 35 | ~45 s cold / ~11 s warm | **~12 s cold / ~3 s warm** |
| `basis` 7d | 7 + 3 tiny | ~9 s cold / ~2 s warm | **~2.5 s cold / ~0.6 s warm** |

**Methodology caveat, stated plainly:** these are summed server-side
`EXPLAIN (ANALYZE)` times over the chunk plan, divided by the concurrency
factor — **not** an end-to-end wall clock. Direct Postgres egress is blocked in
the environment this lane was built in, so the endpoint could not be driven
against the live pantry (see §5). `runtime_ms` is in every movers payload; read
the real number off the first deploy.

**The honest read:** 1d is interactive. **7d dart is not, on a cold cache.** The
5-minute cache carries the repeat cost, but the first 7d dart request after a
dispatch will take double-digit seconds. Recommendation: the movers page's window
toggle should **default to 1d**. If cold 7d proves painful, the fix is a warmed
cache (a scheduled poke after each dispatch), *not* a wider chunk — every wider
shape measured above is worse.

**No migration requested.** No index would fix this: the aggregate needs `lmp`
from the heap, which neither the PK nor `idx_lmp_snap_market_time` covers. A
covering index on a 6.8 GB hot tier to serve one screen is not a trade I would
make without the captain, so `CONCURRENTLY`/slot-122 never comes into play.

---

## 4. Acceptance 2 — one node's package spot-checked by hand

Node: **`ALAMT3G_7_B1`** (AES Alamitos, `GEN`, area `CA`, paired `TH_SP15_GEN-APND`
→ SP15, 33.765978 / −118.103663).

### (a) One HE's DART re-derived from raw DA/FMM rows

Raw rows, `2026-07-29` HE20:

| market | interval | lmp |
|---|---|---|
| DAM | 0 | 99.50456 |
| RTPD | 1 | 65.27968 |
| RTPD | 2 | 62.13957 |
| RTPD | 3 | 63.56517 |
| RTPD | 4 | 63.27074 |

By hand: `Σ RTPD = 254.25516`; `/ 4 = 63.56379` = FMM(HE20).
`DART = FMM − DA = 63.56379 − 99.50456 = **−35.94077**` — negative because RT
settled *below* the day-ahead schedule that hour, which is the correct reading of
the ratified sign.

Endpoint agrees to 4 dp (`rt_intervals: 4` carried alongside).
Pinned: `test_acceptance_one_he_dart_re_derived_from_raw_rows`.

### (b) One month's 7x16 basis re-derived from raw node+hub rows

`2026-07`, node DAM vs SP15 DAM, HE7–HE22 all days:

```
matched 7x16 hours   n = 112
Σ node DAM lmp         6181.79594
Σ hub  DAM value       5590.52533

node_7x16 = 6181.79594 / 112 = 55.194607
hub_7x16  = 5590.52533 / 112 = 49.915405
basis     = 55.194607 − 49.915405 = 5.279202
```

Endpoint ledger row: `node_7x16: 55.1946`, `hub_7x16: 49.9154`,
`basis_7x16: 5.2792`, `n_hours: 112`, `n_days: 8`, `complete: false`.

`112` is the partial-edge arithmetic: 1 (07-22 opens HE22, only HE22 is in-block)
+ 6 × 16 (07-23…07-28) + 15 (07-29 ends HE21) = 112. Pinned independently in
`test_acceptance_partial_edge_dates_sum_to_112_onpeak_hours`.

Also confirmed on real data: `avg(node − hub)` equals `avg(node) − avg(hub)` to
the last decimal (the join is balanced), so the ledger's difference-of-averages
is safe. Pinned so they cannot drift.

### (c) Envelope sanity (acceptance 3)

Bands monotone `p5 ≤ p25 ≤ p50 ≤ p75 ≤ p95`, verified against the SQL
`percentile_cont` figures and pinned two ways: parametrized over six sample
shapes (including N=1, all-equal, and signed-zero), and over the wire for every
HE in the package response. `_an_percentile_cont` re-implements Postgres'
linear interpolation explicitly so the SQL cross-check and the code agree
exactly rather than approximately.

Real N=7 envelope for this node showed HE20 `mean 138.776` against
`p50 −3.469` / `p95 719.161` — a single scarcity evening dominating a 7-sample
mean. Correct arithmetic, and a good argument for the room leading with the
median band rather than the mean.

---

## 5. What I could NOT verify, and why

**Direct Postgres egress (port 5432) is blocked in this build environment.**
Confirmed: connection timeouts to every resolved address for the Neon endpoint;
only the Neon HTTP API path works.

So the split is:

| verified how | what |
|---|---|
| **Against the live pantry** | every SQL shape (`EXPLAIN (ANALYZE)` + real result sets), the depth report, pairing coverage, the identity/search/chunk queries as literal strings, and both hand-derivations |
| **Against real rows, in-process** | all the arithmetic — the pure builders are driven by the measured figures |
| **Fake pool** | routing, params, error contract, caching, headers, shape guards |
| **NOT verified** | end-to-end wall clock, and the app actually opening a pool against Neon |

The SQL strings were each executed against the live database (with literals
substituted for the bound params) so none of them can be a syntax error. What
remains unproven is only the transport. **First deploy should hit all three
routes and read `runtime_ms` off `/api/analytics/movers?window=7d&metric=dart`.**

---

## 6. Notes for Part 2 (the dashboard PR)

- **The DART sign trap.** `dart` here is `FMM − DA` (positive = RT above DA).
  The older `spread` field on `/api/timeseries/caiso-hub-lmp` and the Watchboard
  `dart` tile are the **opposite** sign. Both live in `main.py`; both are
  correct on their own endpoints. This lane never uses the name `spread` so the
  two cannot be confused — but do not reuse a chart component that assumes the
  hub reader's sign without flipping it.
- **Absence is the common case, not an error.** ~89% of nodes return
  `basis.available: false`. The room needs a real "no CAISO trading hub for this
  node" state for the basis panel and the ledger table — not a spinner, not an
  error toast, not zeros.
- **Caption the depth honestly.** `depth.depth_days` is 8. The spec's draft
  caption "distribution over 21 days banked" is wrong; use the field.
- **Ledger is one row today.** The copy-paste-clean monthly table will show a
  single `2026-07` line with `complete: false`. Worth rendering the completeness
  flag visibly — an asset manager lifting a partial-month 7x16 average into a
  Thursday update needs to see that it is 8 days, not a settled month.
- **`name` is often null.** Only 376 priced nodes carry an Atlas plant name.
  Render `pnode_id` as the primary label with `name` as a subtitle when present.
- **Movers: default the window toggle to 1d** (see §3).
- Shape-guard mirrors for all three payloads are in
  `tests/test_analytics.py::_passes_is_node_package` / `_passes_is_movers` /
  `_passes_is_search` — mirror those in the client's guards, per the outlooks
  pattern.

## 7. Out of scope, untouched

Constraint-correlation module · user budget/forecast overlays ·
ownership/generation layers · ERCOT · any Atlas change beyond deep-link-ready
identity fields · the write-up voice (charter module 7). No migrations. No
changes to any existing endpoint, and no existing test modified.
