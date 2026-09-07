# Handback — 2026-09-06 — Model Room cycles endpoint: O(bank) → O(1)

**Repo:** `energylake-api` · **Branch:** `claude/new-session-6yvel9` (pushed; captain merges)
**Spec:** `cc_spec_2026_09_06_model_room_cycles_perf.md` · **One concern:** `/api/model-room/cycles` latency.

---

## WHAT SHIPPED

`GET /api/model-room/cycles` no longer walks R2. It reads the render ledger
`d2_render_runs` in one indexed query and formats the same envelope.

| | before | after |
|---|---|---|
| store | R2 `list_objects_v2` walk | Postgres `d2_render_runs` |
| calls per request | 1 per model root + 1 per date + 1 per cycle | **1** |
| cost scales with | the archive | nothing (indexed, bounded by `days`) |
| measured | 14,061–15,938 ms (499 ×4), p95 bucket 30,000 ms | **0.297 ms** query (EXPLAIN ANALYZE) |
| R2 dependency | required (503 without a token) | **none** |
| 200 cache | *(absent)* | `public, s-maxage=60, stale-while-revalidate=300` |

Three files:

- **`main.py`** — the re-base. `_MODEL_ROOM_CYCLES_SQL` + the rewritten
  `model_room_cycles`; `_model_room_cutoff` as the single window definition;
  the old walk lifted out of the request path into `_cycles_from_r2_walk`
  (the parity arbiter — see the STOP below). `/api/model-room/frame` untouched.
- **`tests/test_model_room.py`** — `FakeDB` beside `FakeR2`; the cycles tests
  re-based onto the ledger, including the falsifier.
- **`scripts/parity_model_room_cycles.py`** — the blocking parity gate, runnable.

---

## STEP 0 — THE PINNED HANDLER (the parity arbiter)

At the branch point (`main`, commit `669b23c`), the implementation was:

> **`main.py:8773-8835`** — `async def model_room_cycles(model: str, days: int)`
> Body: `_MODEL_ROOM_MODEL_RE` check → `_model_room_configured()` 503 gate →
> `_get_r2_client()` → the nested `_list_cycles()` closure
> (`_r2_common_prefixes` over the model root → filter date dirs by the
> `cutoff_token` → `_r2_common_prefixes` per date → `_cycle_is_available`
> per cycle) → `run_in_threadpool` → `found.sort(reverse=True)` → envelope.

It is preserved **verbatim** as `main._cycles_from_r2_walk` (`main.py:8774`),
lifted out of the request path with only two mechanical changes: the closure
became a module-level function, and the shared `_model_room_cutoff` replaced
the inline cutoff arithmetic (same value). Its three helpers
(`_r2_common_prefixes`, `_cycle_manifest_key`, `_cycle_is_available`) are
unchanged.

**It is not a fallback.** `grep` confirms the only caller anywhere is the parity
script:

```
main.py:8774  def _cycles_from_r2_walk(...)          # definition
scripts/parity_model_room_cycles.py:62  main._cycles_from_r2_walk(model, days)
```

No route, no helper, and no exception path in the serving code reaches it.

---

## STOP — THE PARITY GATE DID NOT RUN HERE

**The spec makes parity blocking, and it has not been run. I could not run it
from this build environment, and I did not fake a receipt for it.**

Two hard blocks, both environmental:

1. **Egress.** The session's proxy answers `403` to `CONNECT
   web-production-497cb.up.railway.app:443` — so the deployed OLD endpoint is
   unreachable. (`recentRelayFailures` in the proxy status endpoint records all
   three attempts; `WebFetch` returns `EGRESS_BLOCKED` for the same host.) Direct
   Postgres on 5432 to `ep-polished-surf-ajuuzi61-pooler.c-3.us-east-2.aws.neon.tech`
   is blocked too — `psycopg_pool.PoolTimeout` after 30 s. Only Neon's HTTPS API
   is reachable, which is how the receipts below were taken.
2. **Credentials.** Railway returns `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`
   **redacted** to a connected OAuth app (`valuesRedacted: true`), so the
   arbiter cannot be pointed at the live archive from here even if egress
   allowed it.

This is the same constraint, and the same posture, as
`scripts/verify_outlook_graphics.py` — a gate that ships runnable rather than
being quietly downgraded to a guess. **Run it, on a box with the production
env loaded:**

```
railway run python scripts/parity_model_room_cycles.py --days 14
```

It runs `main._cycles_from_r2_walk` (OLD) against live R2 and
`main._MODEL_ROOM_CYCLES_SQL` (NEW) against live Postgres for `gfs`, `ifs`,
`aifs`, asserts the payloads are equal, and prints the diff cycle by cycle
labelled by direction (`R2 manifest present, NO ledger row` vs `ledger row
banked, NO R2 manifest`). Exit 0 iff every model matches.

**Consequence, stated plainly:** the arbiter is **NOT deleted in this PR**. The
spec says delete it in the same PR *once the gate passes*; the gate has not
passed, so deleting it would destroy the only thing that can arbitrate. Deleting
`_cycles_from_r2_walk`, its three R2 helpers, and the parity script is the
gate's receipt — a two-minute follow-up commit once you have run it, and the
docstring at `main.py:8774` says exactly that.

**If it diverges: STOP.** Do not reconcile in code. A divergence means the
archive and the ledger disagree about what is published — either the pantry
banked a row for a cycle whose manifest never landed, or wrote a manifest
without banking the row. Both are bank-integrity findings worth more than this
lane.

---

## RECEIPTS

### 1 — Schema, verified live (`information_schema.columns`, 2026-09-06)

Matches the spec's listing exactly. One correction worth having:

> **`manifest_sha` is `NOT NULL`** in the live schema — the spec's column list
> did not say so, and it decides the publication-semantics question below.

`ladder_rungs` and `ladder_sha` are the only nullable columns.

### 2 — Publication-semantics census (the spec's step-1 question)

| measure | count |
|---|---|
| rows in `d2_render_runs` | 1,079 |
| `manifest_sha IS NULL` | **0** |
| `manifest_sha = ''` | **0** |
| distinct `(model, run_date, cycle)` | 260 |
| date span | 2026-07-28 → 2026-09-06 |

In the 14-day window, per model, distinct cycles vs cycles carrying a
`manifest_sha`: **gfs 42/42 · ifs 23/23 · aifs 24/24.**

**Ruling taken:** every banked cycle carries a `manifest_sha`, so per the spec I
filtered on it — `AND manifest_sha IS NOT NULL` is in the shipped SQL. It is a
**no-op against today's table** (the `NOT NULL` constraint guarantees it) and it
is written anyway: manifest-presence is D-07-23-01's publication signal, and the
seam belongs in the SQL where a future nullable column or a partial bank cannot
quietly widen the answer. `test_cycles_excludes_unmanifested_row_seam` pins it.
No guessing was required and nothing was left for the gate to arbitrate here.

### 3 — `EXPLAIN ANALYZE` (no migration needed)

The shipped query, `model='gfs'`, cutoff `2026-08-25`:

```
Sort (actual time=0.264..0.266 rows=42 loops=1)
  Sort Key: run_date DESC, cycle DESC
  ->  HashAggregate (actual time=0.245..0.251 rows=42 loops=1)
        Group Key: run_date, cycle
        ->  Seq Scan on d2_render_runs (actual time=0.072..0.168 rows=435)
              Filter: ((run_date >= '2026-08-25') AND (model = 'gfs'))
              Rows Removed by Filter: 644
Planning Time: 0.079 ms   Execution Time: 0.297 ms
```

**A supporting index exists — no migration is required, and none was applied.**
`d2_render_runs_pkey` is `(model, run_date, cycle, param, region)`; its two
leading columns are exactly this predicate. The planner picks a seq scan today
only because the table is 1,079 rows in 54 pages, where a scan is genuinely
cheaper. Forced (`SET LOCAL enable_seqscan = off`), it uses the index:

```
->  Bitmap Index Scan on d2_render_runs_pkey (actual time=3.250..3.251 rows=512)
      Index Cond: ((model = 'gfs') AND (run_date >= '2026-08-25'))
```

So the query is index-served by construction and stays O(window) as the ledger
grows — which is the property that matters, since growth is what broke the walk.

### 4 — NEW payload, live, `days=14` (2026-09-07 UTC, cutoff 2026-08-25)

Taken over Neon's HTTPS API with the shipped SQL and the shipped formatting.
Counts: **gfs 42 · ifs 23 · aifs 24 cycles.** Newest-first, `date` = `YYYYMMDD`,
`cycle` two-digit zero-padded. Head of each:

```
gfs   20260906/12  20260906/06  20260906/00  20260905/18 … 20260825/00   (42)
ifs   20260906/12  20260906/00  20260905/12  20260905/00 … 20260825/00   (23)
aifs  20260906/12  20260906/00  20260905/12  20260905/00 … 20260825/00   (24)
```

The gaps are real and expected (`gfs` 20260901 has no 00Z; `ifs`/`aifs` bank
00Z/12Z only) — they are what the ledger holds, and precisely what the gate
must confirm the archive agrees with.

### 5 — Latency: origin measured, end-to-end NOT measured

Origin query cost is above: **0.297 ms**. The spec's acceptance measurement —
20 sequential production-shaped HTTP calls post-deploy, p50/p95, target
p95 < 500 ms — **could not be taken from here** (the same egress block), and it
is a post-deploy measurement in any case. It is yours to take after merge,
alongside the zero-499s hour in the Railway logs.

### 6 — Test suite

```
tests/test_model_room.py   24 passed
tests/                   1681 passed, 1 warning in 8.31s
```

No other route touched. `git diff --stat`: `main.py`, `tests/test_model_room.py`,
`scripts/parity_model_room_cycles.py`.

---

## THE FALSIFIER

`test_cycles_makes_zero_r2_calls` stands a **fully provisioned** `FakeR2` in the
path, stocked with exactly the render keys the old walk would have found, and
asserts `fake.calls == []` — zero R2 calls of any verb on the cycles path. Not
"fewer". A regression that reintroduces even one `MaxKeys=1` probe per cycle
fails here, not in a Railway log a week later.

`test_cycles_503_when_db_unavailable` is its twin for the no-fallback rail: R2
provisioned and stocked, DB failing, and the assertions are **503** *and*
`fake.calls == []`. The handler could have walked, and does not.

---

## DEFECTS AND JUDGMENT CALLS

**1 — `manifest_sha` is `NOT NULL`, which the spec's schema listing omitted.**
Not a defect, but it is what let the publication-semantics question be settled
by measurement rather than left to the gate. Recorded above.

**2 — The `days` window is off by one from the spec's SQL.** The spec's shape
reads `run_date >= current_date - %(days)s::int`. The old handler's window is
`_utcnow().date() - timedelta(days=days - 1)` — `days` dates **including today**.
`current_date - 14` is a **fifteen**-date window, so shipping the spec's SQL
literally would have put one extra date in every response and failed the parity
gate at the boundary for a reason that had nothing to do with the store. I kept
the old arithmetic, hoisted into `_model_room_cutoff` so the arbiter and the live
read share one definition and cannot drift. `test_cycles_cutoff_param_is_inclusive_window`
pins it.

**3 — The cutoff is a bound date parameter, not `current_date`.** The window is
the app's UTC clock (`_utcnow`) — the clock the walk used and the clock the tests
freeze. Computing it in SQL would silently hand the window to the database
session's clock, which is a different clock with a different failure mode.
(Neon's session `TimeZone` is `GMT` today, so they agree — that is luck, not a
contract.)

**4 — DELIBERATE BEHAVIOUR CHANGE: the R2-unprovisioned 503 is gone from
`/cycles`.** Before, no R2 token meant 503 on this route. It no longer reads R2,
so that gate would have been a guard wired to a store the route does not use —
and, worse, one that can dark the Viewer's cycle index during an R2 outage the
endpoint is now immune to. Dropped, and pinned in the opposite direction by
`test_cycles_served_with_r2_entirely_unprovisioned` (200 with all four `R2_*`
vars blank). This is the one status-code difference from the pinned handler; it
is unreachable in production (R2 is provisioned) and it does not affect the
parity gate, which compares 200-path payloads. `/frame` keeps its 503 — it is
still an R2 proxy. The old test asserting the 503 was replaced, not deleted
quietly.

**5 — The 502 path is gone with the walk.** `cycle listing failed: {code}` was
an R2-transport status. The ledger read's failure posture is the platform's
standard `503 db unavailable: …`, per the spec.

**6 — `_cockpit_read` was reused rather than a new reader written.** It is the
lane's retry-once-on-a-stale-connection helper (Neon idle-closes sockets), it is
what `/api/nodes/facets` next door uses, and its `assert _pool is not None`
raises inside the route's `try`, so an unopened pool answers 503 like any other
DB unavailability rather than 500.

**7 — Not observed, but worth your eye at the gate.** The ledger's earliest row
is **2026-07-28**, yet `d2/renders/gfs/20260723/00Z/manifest.json` is live in R2
(the D-07-23 receipt, still pinned in the test suite). That is 40 days back —
far outside any `days ≤ 31` window, so it cannot affect this endpoint — but if
the ledger simply does not go back as far as the archive, a `days=31` parity run
would diverge on old dates for a benign reason. Worth knowing before you read a
wide-window diff as a bank-integrity finding.

---

## NOT DONE, AND NOT ATTEMPTED (per spec)

- `/api/weather/delta-board` (11,826 ms) — same disease, own lane.
- Dashboard single-flight dedup — dashboard repo. The `s-maxage=60` shipped here
  blunts the four-parallel-request cost at the CDN but does not fix it at source.
- The STILL LOADING banner stays. This lane removes its cause.
- **No migration applied.** None needed (receipt 3). If you ever want the seq
  scan gone before the table grows into the index, that is your sitting, not
  mine.
