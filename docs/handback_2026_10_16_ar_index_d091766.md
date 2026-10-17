# Handback d091766: the AR index — STOP-D, nothing banked to serve

**Lane:** d091766 · **Repo:** energylake-api · **Branch:** `claude/exciting-hamilton-gtdhfd`, from main at `b399c67`.
**Ran:** 2026-10-17, 10:28Z–10:50Z (container clock). **Environment:** EL 6.
**Rulings held:** D-09-25-245 · show_v1 (migration 292) · D-09-25-75 (CLAUDE.md) · D-08-02-L · D-09-05-T · CLAUDE.md d091551.

**Scope kept:**
- Branch only. No PR, no merge, no deploy.
- **No Neon write.** Every statement ran in a transaction opened `SET TRANSACTION READ ONLY`, with `transaction_read_only` read back `on` before any read. Every receipt below is from such a transaction.
- **No R2 read.** None fetched; `display.energylake.io` was not called.
- **Network:** Neon connector (reads only); GitHub — `energylake-api` and `energylake-pantry`, read only.
- **No route, no test, no constant written.** STOP-D fired, so §0 is the whole deliverable, per the spec's "write §0 and stop".

**Read first:** this repo's `CLAUDE.md`; `tropics_index.py` and `main.py`'s tropics section (`TROPICS_STATEMENT_TIMEOUT`, `_DDCache`, `DISPLAY_BASE`, the `LATERAL` reads); the d091741/d091746 tests and handbacks; pantry `ar/cells.py`, `ar/points.py`, `ar/score.py` at the merged d091765.

---

## 0. STOP-D: `ar_display_objects` does not exist

**The lane does not open.** `ar_display_objects` is absent from Neon — not empty, absent. There is no `coast_ivt` row because there is no table to hold one, so there is nothing banked for the API to index, and `/api/weather/ar/index` would have no `inits` to list and no `registry` to name.

### 0.1 The fire-when condition, half met

The spec's trigger was "d091765 is merged and migration 324 applied". The first half holds; the second does not.

| Half | State | Receipt |
|---|---|---|
| d091765 merged | **yes** | pantry default branch carries `b786fe7` (the publish, score job, witness, migration 324) and `4c82eb2` (its handback), 2026-10-16 22:16Z/22:20Z |
| migration 324 applied | **no** | `schema_migrations`: max version **323** (`323_hrrr_nbm_model_runs_hourly.sql`, applied 2026-10-17T00:04:20Z), 308 rows, **no row for 324 at any status** |
| `ar_display_objects` present | **no** | `to_regclass('public.ar_display_objects') IS NULL` → `true`; `pg_class` under `public` holds only `ar_runs`, `ar_landfall_latest`, `ar_scores` and their three PK indexes |

d091765's own commit message says it: "migrations/324: ar_display_objects, ar_truth_days (**written, NOT applied**)". So the publish lane shipped its code and left the DDL for the architect. Until 324 is applied *and* `scripts/ar_publish.py` has run, the index route has no subject.

**What unblocks this lane, in order:** apply migration 324 → run `scripts/ar_publish.py` for at least the newest banked cycle → re-fire d091766. Nothing in this repo is in the way.

### 0.2 What Neon does hold (read 2026-10-17 10:32:39.679Z, `transaction_read_only = on`, PostgreSQL 17.11)

| Table | Rows | Size | State |
|---|---|---|---|
| `ar_runs` | 16 | 64 kB | 16 cycles, `2026-10-09 12Z` → `2026-10-17 00Z`, every one `status = complete`, `members_present = 31`, `steps_with_odds = 93`, `missing` empty, one `method_hash` (`3092ec738846`) throughout |
| `ar_landfall_latest` | 6,603 | 2,440 kB | newest init only (`init_ts` min = max = `2026-10-17T00:00:00Z`), `level_set = sub7_v1`, 71 point codes × 93 valid steps, `lead_h` 0…384, `n_present = 31` on every row |
| `ar_scores` | **0** | 32 kB | born empty; the backtest is written but not run |
| `ar_display_objects` | — | — | **absent** |

`tc_named_system` and `tc_rule_version` are NULL on all 6,603 rows (0 distinct non-null values) — as 292's ledger note said they would be.

Migration **292 is applied** (2026-10-08T23:16:33Z, `292_ar_ivt_bank_shadow.sql`), so `ar_scores` already carries show_v1 in its schema. Its CHECKs, verbatim:

- `ar_scores_show_rule_v1` — `show IS NOT TRUE OR (show_rule = 'show_v1' AND n_eff >= 60 AND event_days_eff >= 14 AND bss_lo90 > 0 AND cardinality(show_reasons) = 0)`
- `ar_scores_like_for_like` — `truth_level_set = level_set`
- `ar_scores_decision_on_all` — `(k_bin = 'all') = (show IS NOT NULL)`
- `ar_scores_region_check` — `baja_socal | central_norcal | or_wa | bc | se_alaska`
- `ar_scores_lead_bucket_check` — `d1-2 | d3-5 | d6-8 | d9-12 | d13-16`
- `ar_scores_threshold_check` — `250 | 500`; `ar_scores_k_bin_check` — `0 | 1-3 | 4-9 | 10-15 | 16-21 | 22-27 | 28-31 | all`

### 0.3 Today every probability is withheld — the gate is the whole route

`ar_scores` holds 0 rows, so under D-09-25-245 **all 50 cells (5 regions × 5 lead buckets × 2 thresholds) are unscored** and every one of the 6,603 landfall rows withholds `p250`, `p500`, `n250` and `n500` with `withheld: {reason: 'not_yet_scored'}`. This is not an edge case to test around; it is the live state the route would serve on its first day, and A2 is the route's main behaviour rather than a corner.

Note for whoever builds it: `n250`, `n500` and `n_present` are **non-null on all 6,603 rows**, so the counts are sitting in the table waiting to leak. The gate has to be applied on the way out, per cell, not inferred from a null.

`ens_mean`, `ctrl` and `max_member` are model IVT quantities and are served ungated (D-09-25-245 §2) — they are present on every row.

### 0.4 Two columns the route must derive, which the spec did not flag

`ar_landfall_latest` has **no `region` column and no `lead_bucket` column**:

```
init_ts, level_set, valid_ts, lead_h, point_code, lat, lon, n250, n500,
n_present, ens_mean, ctrl, max_member, absent_members, tc_named_system, tc_rule_version
```

But a cell is `(region, lead_bucket, threshold)`, so **every gate decision requires deriving both** — region from `lat` through the pantry's bands, lead bucket from `lead_h` through the pantry's edges. The pinned constant the spec asks for is therefore not a nicety for A4; it is on the critical path of every `/landfall` row. Get a band edge wrong and a probability is gated by the wrong cell's verdict, which is the exact failure D-09-25-245 exists to prevent.

### 0.5 The pinned edges, reproduced (to be confirmed, not trusted)

The spec says to copy the edges "with that commit sha as a pinned constant". d091765 **is** merged, so they exist. From the pantry at `b786fe7` (tree `37a1bc2`):

- `ar/cells.py` blob `1a1bf3b335d0473d1a3feb12ddaba21ebb91ecd4` — `edges()`, `edges_sha256()`, the 50-cell `universe()`
- `ar/points.py` blob `c6326a62ca1cd97412827d9b409fbb9f57cb7d07` — `REGIONS`
- `ar/score.py` blob `b7c12a53850e4e122bee918e8654b5d08691362d` — `LEAD_BUCKETS`, `THRESHOLDS`, `K_BINS`, `SHOW_RULE`

`ar/cells.py` has not been touched since `b786fe7` introduced it.

| Lead bucket | `(gt_h, le_h]` | | Region | `[lat_min, lat_max]` | Coast points |
|---|---|---|---|---|---|
| `d1-2` | (0, 48] | | `baja_socal` | [25.0, 34.5] | 20 |
| `d3-5` | (48, 120] | | `central_norcal` | [35.0, 42.5] | 16 |
| `d6-8` | (120, 192] | | `or_wa` | [43.0, 49.5] | 14 |
| `d9-12` | (192, 288] | | `bc` | [50.0, 55.5] | 12 |
| `d13-16` | (288, 384] | | `se_alaska` | [56.0, 60.0] | 9 |

Thresholds 250, 500; exceedance `>=`. Rules as the pantry words them: a point belongs to the region whose `[lat_min, lat_max]` holds its latitude (the half-degrees between regions sit in the region below); bucket where `gt_h < lead_h <= le_h`, and **`lead 0` is in no bucket and never shows**.

Reconciled against the live bank (same read-only transaction), which the bands and edges partition exactly:

- by region — 20 + 16 + 14 + 12 + 9 = **71** points, 1,860 + 1,488 + 1,302 + 1,116 + 837 = **6,603** rows, **no point falls outside a band**
- by bucket — `d1-2` 1,136 · `d3-5` 1,704 · `d6-8` 1,420 · `d9-12` 1,136 · `d13-16` 1,136, plus **71 rows at `lead_h = 0`** in no bucket; 71 + 6,532 = **6,603**

Those 71 lead-0 rows (one per point, the init hour) can never be shown under the pantry's own lead rule. The route must withhold them on that ground, which is **not** `not_yet_scored` — it is a cell that does not exist. The spec's three absence reasons (`no_init`, `not_published`, `not_yet_scored`) do not cover it. Flagging it rather than picking one.

**`edges_sha256` — reconstructed, unverified:** `5b0068645d1c3514365f50f15a6900c15bdd6758cde6c1580c2e82ccfb6cc0fe` (1,204-byte canonical JSON). This is my reproduction of `edges()` from the three blobs above, **not** the pantry's own output. The next firing must pin what `scripts/ar_cells.py --edges` actually prints and must not take this number on faith; I could not run the pantry's script from here without cloning it, which this lane did not need.

### 0.6 What this spec got wrong

1. **STOP-D's premise.** It reads as though the only question were whether the publish had run. The binding constraint is one step earlier: migration 324 is not applied, so the table cannot exist and the publish cannot have banked anything. d091765 being merged — which it is — was never sufficient.
2. **The cells' vocabulary was already in Neon.** The spec sends the lane to the pantry for the bucket and region *names*; migration 292 has pinned both enumerations in `ar_scores`' CHECKs since 2026-10-08. What genuinely needs the pantry is the **numeric** edges — the lat bands and the hour boundaries — because those are what map a row to a cell, and §0.4 is why that matters.
3. **The show_v1 four-S framing does not match the CHECK.** The spec gives S1–S4 as one rule. In the schema, S1 and S2 are terms of `ar_scores_show_rule_v1`, S4 is a separate CHECK (`truth_level_set = level_set`), and **S3 (calibration) appears in no CHECK at all** — it is enforced by the scorer and surfaces only as a `show_reasons` entry, which the CHECK then requires to be empty. A test that asserts "the CHECK enforces S1–S4" will be asserting something untrue.
4. **`region` and `lead_bucket` were assumed to be columns.** They are not (§0.4).
5. **The absence vocabulary is short one reason** — the 71 lead-0 rows (§0.5).
6. **"the first banked rows are the vectors" (D-09-05-T) has nothing to bank yet.** `ar_scores` is empty and `ar_display_objects` absent, so A3 will need a fixture row, as the spec allows — but it should be said plainly that A3 is the only test in the set that cannot be built from Neon's own rows, and that the real `ar_scores` rows become the vector the moment the backtest runs.

### 0.7 Not done

Everything after §0: the two routes, `tests/test_ar_index_d091766.py` (A1–A5, X1, R), the EXPLAIN receipts, the banked fixture bodies and their shas for the dashboard lane. No file in this repo was changed; this handback is the only addition.


---

# Re-fire, 2026-10-17: STOP-D cleared, the six findings ruled, the AR index built

**Ran:** 2026-10-17, 12:48Z–13:30Z (container clock). **Environment:** EL 6.
**Branch:** the lane branch `claude/exciting-hamilton-gtdhfd` at `c06c82c` (§0, kept as written above) was fast-forwarded into this session's designated push branch, **`claude/ar-index-refire-qnt7jm`**, and every commit of this firing is on that branch. The paste names the lane branch; the session harness allows pushes only to its designated branch. History is continuous: §0's commit is this branch's parent. No PR, no merge, no deploy.

**Scope kept:**
- **No Neon write.** Every statement ran in a transaction opened `SET TRANSACTION READ ONLY`, with `transaction_read_only` read back `on` in the same transaction (each receipt below carries it). The EXPLAIN (ANALYZE) buffer lines show `written=2`: that is the buffer manager evicting pages dirtied by hint bits, not a row write.
- **No R2 credential, no R2 read.** Public HTTPS reads of `display.energylake.io`: the 2026-10-17 00Z `coast_ivt` object and the registry, both GETs and both sha-checked against their ledger rows, and one HEAD of the same cycle's odds object (§6.4).
- **Pantry, read only.** `energylake-pantry` was attached read-only and `af0aeb9` checked out in a scratch worktree, so that `python scripts/ar_cells.py --edges` could be run there (§4) and migrations 145, 292 and 324 copied byte for byte. Nothing was written to the pantry.

## 1. STOP-D, re-checked: clear

Read `2026-10-17T12:49:25.166Z`, `transaction_read_only = on`, PostgreSQL 17.11:

| `ar_display_objects` kind | rows | inits | newest `published_at` |
|---|---|---|---|
| `coast_ivt` | 16 | 2026-10-09 12Z → 2026-10-17 00Z | 12:19:10.251Z |
| `odds` | 16 | same 16 | 12:19:10.917Z |
| `registry` | 1 | — | 12:18:48.397Z |

`schema_migrations` holds 324 (`committed`, applied 2026-10-17T10:35:28Z, attestation `6db58cda4d05d89b772e2fe6a0966587`); max version 328. `ar_runs` 16, `ar_landfall_latest` 6,603 (init 2026-10-17 00Z), `ar_scores` **0**. The lane opens.

## 2. The routes

Code: `ar_index.py` (SQL, pinned edges, shaping) and a new `main.py` section after the Tropics index (`AR_STATEMENT_TIMEOUT = "5s"`, `_ar_index_cache` and `_ar_landfall_cache` as single-flight `_DDCache`s with a 300 s TTL that are never served stale, the `_solar_serve` 503 contract). It mirrors `tropics_index.py` and the Tropics section: one transaction per build, `SET LOCAL statement_timeout` first, `DISPLAY_BASE = "https://display.energylake.io/"`, and no R2 or object read.

### `GET /api/weather/ar/index`

```
{ level_set: "sub7_v1", newest_init, inits_published,
  inits: [ { init_ts, status, members_present, steps_with_odds, method_hash,
             coast_ivt: { url, sha256, bytes, published_at, absence },
             odds_published } ],                                   // newest first
  registry: { url, sha256, bytes, published_at, absence },
  scoring: { rule: "show_v1", cells_scored, cells_shown, cells_total: 50, scored_at, scorer_hash,
             cells: [ { region, lead_bucket, threshold, scored, show, show_reasons, withheld } ],
             show_rule },
  edges_sha256, edges: { edges_sha256, pin, pinned, source, regions, lead_buckets, thresholds,
                         exceedance, region_rule, lead_rule },
  bias_note, attribution, absence, order, index_rule, reasons, not_served, timestamps, cache }
```

An init is an `ar_runs` cycle. Its `coast_ivt` is the `ar_display_objects` row at the key 324's `ar_display_cycle_key` CHECK assigns to that cycle; if there is no such row, it is absent with reason `not_published`. `odds_published` is a boolean. The odds object's URL is **never served**, because it is a bank artifact (`not_served.odds_object`). `bias_note` and `attribution` are the publisher's own strings (pantry `ar/publish.py` `BIAS`, `ATTRIBUTION`), and the test checks them equal to the banked object's `bias` and `attribution`. When `ar_runs` is empty the route still answers 200, with `absence: no_init` and the registry `not_published`.

Today: 16 inits, all published, all `odds_published`. The registry is `points_v1.6461743c.json`, and 0 of 50 cells are scored.

### `GET /api/weather/ar/landfall?init=`

```
{ level_set, init_ts, init_requested, units: "kg m-1 s-1",
  rows: [ { point_code, lat, lon, region, valid_ts, lead_h, lead_bucket,
            n_present, ens_mean, ctrl, max_member, absent_members,
            [p250, n250,] [p500, n500,]                         // ONLY where that threshold's cell shows
            withheld: { "250"?: { reason, rule? | show_reasons? }, "500"?: {…} } | null } ],
  counts: { rows, points, served: { "250", "500" }, withheld_by_reason },
  scoring, absence, order, edges_sha256, edges, landfall_rule, show_rule, reasons,
  bias_note, attribution, timestamps, cache }
```

`init` may be `YYYYMMDDHH` or ISO 8601 UTC, and must be 00Z or 12Z; anything else is a 400. The default is the cycle `ar_landfall_latest` holds. An init the table does not hold (it keeps only the newest) gets a 200 with `absence: no_init`, and the detail names the cycle it does hold.

**The gate.** A cell is a (region, lead_bucket, threshold) triple, so the gate runs **per threshold**: `p{t}` = `round(n{t} / n_present, 4)` and `n{t}` appear only where the newest scoring's `k_bin = 'all'` row for that cell says `show = true`. Each threshold that is not served becomes a key of `withheld`, and the row then carries neither its probability nor its count. The keys are absent, not null. The reasons:

- `not_yet_scored`
- `not_shown`, with the scorer's `show_reasons` verbatim
- `no_cell`, with `edges().lead_rule` or `region_rule` verbatim (ruling 4)
- `edges_pin_mismatch` (ruling 1)

`ens_mean`, `ctrl`, `max_member`, `n_present` and `absent_members` are served on every row.

Region is derived **once per point** from the pinned bands. Bucket is derived as `gt_h < lead_h <= le_h`. If a point's rows disagree with each other, or with the latitude in their `point_code`, the response is a **500 that names the point** (`ar landfall refused: point coast_47.0N's region cannot be derived once: …`).

Today: 6,603 rows, 71 points, **0 values served at either threshold**. Withheld: `not_yet_scored` 13,064 (6,532 rows × 2 thresholds) and `no_cell` 142 (71 lead-0 rows × 2). Body size is 2.08 MB uncompressed; main's `GZipMiddleware` serves it at about 133 KB.

### The reads

All five reads name level set `sub7_v1`, and no read uses `DISTINCT ON`:

1. `INDEX_SQL`: every cycle, with one LATERAL per (cycle, kind). Each LATERAL is an exact `ar_display_objects_pkey` probe on `(level_set, kind, display_key)`, with `LIMIT 1` (§3).
2. `REGISTRY_SQL`: the `(level_set, 'registry')` PK range, newest publish, `LIMIT 1`.
3. `SCORES_SQL`: the newest `scored_at`, read backwards with `LIMIT 1`, then a LATERAL of its `k_bin = 'all'` rows. Show and show_reasons are served as written, and no S is recomputed.
4. `LANDFALL_INIT_SQL`: the first row of the PK's `level_set` prefix, `LIMIT 1`.
5. `LANDFALL_SQL`: the cycle's rows in PK order.

## 3. EXPLAIN (ANALYZE, BUFFERS) receipts, Neon

The full plans are in `docs/receipts/ar-index-d091766/neon_plans.txt`. The statements, with parameters bound exactly as the routes bind them, are in `explains.json` (generated by `explains.py`).

Table sizes at the read (`2026-10-17T12:58:03.750Z`, ro on):

| Table | Rows | Total size |
|---|---|---|
| `ar_runs` | 16 | 64 kB |
| `ar_display_objects` | 33 | 32 kB |
| `ar_landfall_latest` | 6,603 | 2,440 kB |
| `ar_scores` | 0 | 32 kB |

**Every table is under 10k rows.**

| Read | Plan | Rows | Exec |
|---|---|---|---|
| `INDEX_SQL` | Seq Scan `ar_runs` (16 rows), then per cycle `Index Scan using ar_display_objects_pkey`, Index Cond `(level_set = r.level_set) AND (kind = 'coast_ivt') AND (display_key = 'ar/gefs/'‖…)`, `rows=1 loops=16`; the odds probe is the same as an Index Only Scan | 16 | 0.164 ms |
| `REGISTRY_SQL` | Index Scan `ar_display_objects_pkey`, Index Cond `(level_set, kind = 'registry')` | 1 | 0.019 ms |
| `SCORES_SQL` | Seq Scan `ar_scores` (0 rows; never analyzed) for the newest `scored_at`, then Index Scan `ar_scores_pkey` Index Cond `(scored_at = a.scored_at) AND (level_set) AND (k_bin = 'all')` (never executed today) | 0 | 0.022 ms |
| `LANDFALL_INIT_SQL` | Index Scan `ar_landfall_latest_pkey`, Index Cond `(level_set = 'sub7_v1')`, Limit 1 | 1 | 0.026 ms |
| `LANDFALL_SQL` | Seq Scan `ar_landfall_latest` (6,603 rows < 10k; the whole table is the answer), Sort | 6,603 | 5.482 ms |

**The receipt that changed the SQL.** The first `INDEX_SQL` (read 12:58:03Z, kept in `neon_plan_index_before_limit1.txt`) had no `LIMIT 1` in its LATERALs. Postgres pulled each one up into a plain join. It then walked the `(level_set, kind)` range eight rows per cycle and dropped the rest by `Join Filter` (`Rows Removed by Join Filter: 120` per kind). That is two index scans that each serve two keys, which D-08-02-L forbids. With `LIMIT 1`, the d091551 shape, each LATERAL stays a per-cycle subquery and probes the whole key. A5 and X1 assert this, and mutation 14 removes the `LIMIT 1` and turns red.

On the cluster (33 rows on one page) the planner prefers a seq scan. A5 therefore proves the key is sargable with `enable_seqscan = off`: each LATERAL's `Index Cond` names all three key columns and no `Join Filter` appears.

## 4. The pinned edges, and their source

The constant is `ar_index.EDGES`, which is pantry `ar/cells.py` `edges()` copied rather than imported (`ar/points.py` `REGIONS`; `ar/score.py` `LEAD_BUCKETS`, `THRESHOLDS`, `K_BINS`, `SHOW_RULE["version"]`).

- **Pin:** `edges_sha256 5b0068645d1c3514365f50f15a6900c15bdd6758cde6c1580c2e82ccfb6cc0fe`
- **Source:** "energylake-pantry scripts/ar_cells.py --edges at af0aeb902b637218f434110722cdf12170618ee5 (edge files last changed b786fe79)"
- **Confirmed here:** `python -I scripts/ar_cells.py --edges` in a worktree at `af0aeb9` printed `AR_CELL_EDGES commit af0aeb902b637218f434110722cdf12170618ee5 edges_sha256 5b0068645d1c3514365f50f15a6900c15bdd6758cde6c1580c2e82ccfb6cc0fe`. `git log af0aeb9..HEAD -- ar/` is empty: the edge files have not changed since `b786fe7`. This matches §0.5's reconstruction and the architect's confirmation, and the canonical bytes are 1,204, as §0.5 said.
- **A4:** `sha256(json.dumps(EDGES, sort_keys=True, separators=(",", ":")))` equals the pin.
- **Refusal:** if the recomputed sha ever differs, both routes refuse every cell (`edges_pin_mismatch`), and `/index` shows `edges.pinned: false`. The test changes one lead edge and finds 0 values served, even with a shown cell in `ar_scores`.

## 5. The banked vectors, with shas (d091767's vectors)

The rows are in `tests/fixtures/ar_index_d091766/`; `bank_from_neon.py` holds the statements and the writer. Each was read as `row_to_json(t)::text` in PK order, joined by `\n`, and checked against the sha-256 Neon computed over the same text. None was constructed.

| Bank | Table | Rows | Neon sha-256 | Read |
|---|---|---|---|---|
| `bank/runs.jsonl.gz` | `ar_runs` | 16 | `2d2bffffe4b3ff67bec31064ac6ff801f693b833da7ad53cb418e6a28b826a7b` | 12:51:19.537Z |
| `bank/display.jsonl.gz` | `ar_display_objects` | 33 | `b040f81f495578520c5f152568392d731861329916d1287fc22463f3c1eb457b` | 12:51:19.539Z |
| `bank/landfall.jsonl.gz` | `ar_landfall_latest` | 6,603 | `4a10bcb635331b8dc776c47d66fad15b564f402257a6d5ea86b678cbaefa6656` | 12:52:21.953Z |

The objects, fetched from `display.energylake.io` (HTTP 200, `application/json`), each with a sha equal to its `ar_display_objects` row:

| Object | Bytes | sha-256 |
|---|---|---|
| `ar/gefs/20261017/00Z/coast_ivt.sub7_v1.json` | 1,199,506 | `2e76eb7e3f7719f503b44f9a6a31d02055197e273e3ad156f72c4873ceb41f5b` |
| `ar/registry/points_v1.6461743c.json` | 43,072 | `6461743c86ea3b726303f239801a7df2a7daf9794be4780f42607df91948525a` |

The DDL, verbatim from pantry `af0aeb9`:

| File | sha-256 |
|---|---|
| `145_schema_migrations_ledger.sql` | `4fd273f8062a908bfde6422552a90a160e831a3074070790e3b3db5091c2deac` |
| `292_ar_ivt_bank_shadow.sql` | `268d49d583a31608994149a566b8cbff568c236e6d1b6126024b63f4b3dbb78b` |
| `324_ar_display_objects_and_truth_witness.sql` | `0b344c0b12e352127043b45f5f29d23ac76d80dc984c025d764baac5c89bbfaa` |

How the cluster is built (`tests/load_bank_d091766.py`, following the d091746 pattern):

- 145's ledger, and the pantry rehearsal's `datasets` stand-in.
- 292 sections 1–3 verbatim. Sections 4–6 need `sources`, `ingestion_freshness` and `model_cycle_liveness`, which the cluster lacks; this is the pantry's own `rehearse_ar_publish_d091765.py` cut.
- 324 **whole and verbatim**. Its pre-check, ten bites and post-check run on every load ("POST-CHECK OK … 10 of 10 bites").
- The banked rows.

The cluster's 44 constraint definitions on the four tables equal Neon's (`neon_constraints.json`), definition for definition.

**The bodies handed back** are in `docs/receipts/ar-index-d091766/bodies/`, with `manifest.json`. `bank_bodies.py` serves them over the cluster, and R1 serves each again and compares it whole:

| Body | sha-256 (of the file) | Bytes | Served over |
|---|---|---|---|
| `index.json` | `9cd48001b97c12db8185b1bcf34506e44213f20ca0708fbbf08ed9932e2eae13` | 22,548 | the bank (`ar_scores` empty) |
| `landfall.json.gz` | `5a6ce4298927a09a0e39f2b427c1333e00ad2cec5db4c596916e753402e7d7c3` | 133,389 | the bank: 0 served, 13,064 `not_yet_scored`, 142 `no_cell` |
| `index_a3_fixture.json` | `569a08d782dd376e0af6e4226646ae5d457ffaac1517103b57aea9b98e5a540d` | 22,546 | **with A3's constructed row** |
| `landfall_a3_fixture.json.gz` | `6737b3ffa5f7f86f8f3478b96317afcb4f99dc406bf30d428acc69a66c51ce7e` | 134,394 | **with A3's constructed row**: 224 `p250`/`n250` served, all `or_wa × d1-2` |

**A3's row is the only constructed vector.** `ar_scores` held 0 rows at every read, so A3 uses `load_bank_d091766.SCORES_FIXTURE`: one `k_bin = 'all'` row, show = true, `or_wa × d1-2 × 250`. It is shaped like 292's own landing probe (n 500, n_eff 120, bss_lo90 0.2, event_days_eff 20, no reasons) and is inserted through 292's CHECKs. **When the backtest's score mode writes the first `ar_scores` rows, they replace this fixture as A3's vector** (bank them with `bank_from_neon.py`'s pattern), and the `_a3_fixture` bodies should be re-cut from them.

## 6. Tests, rehearsal, suite

`tests/test_ar_index_d091766.py` has **28 tests, all green**:

- **B ×4:** the bank is Neon's; the objects' shas are the ledger's; 324 runs whole; the constraints equal Neon's.
- **A1 ×3:** 16 inits and the census; one cycle `not_published`; `no_init` on both routes.
- **A2:** every probability and count withheld; the IVT quantities equal the bank row for row. The banked counts are present, 3,357 rows with `n250 > 0`, and none is served.
- **A3 ×4:**
  - Exactly `or_wa × d1-2` `p250`/`n250`, 224 rows (14 points × leads 3–48), no `p500`/`n500`.
  - `not_shown` carries `show_reasons` verbatim.
  - show_v1 as the schema holds it. The S1 terms (`n_eff >= 60`, `event_days_eff >= 14`) and S2 (`bss_lo90 > 0`) are in `ar_scores_show_rule_v1`, and S4 is `ar_scores_like_for_like`. Five bites are refused by those CHECKs. No CHECK names a calibration column: an uncalibrated row with no `show_reasons` **lands**, so the CHECK does not enforce S3, and no test claims it does.
  - The route recomputes no S.
- **A4 ×2:** the sha is the pin; a changed constant serves no cell.
- **A4b ×2:** the derived region equals the object's for **71 of 71** points, and the served region, lat and lon equal the object's. The eight band-edge points are asserted by name. A point with a disagreeing row is a 500 naming `coast_47.0N`.
- **A5 ×4:** the timeout is the first statement on both routes and every read binds `level_set = sub7_v1`; a memo hit reads nothing (`X-Cache: hit`); each LATERAL is an exact-key probe ending `LIMIT 1`, and no SQL has `DISTINCT`; on the cluster each Index Cond names the whole key.
- **A6 ×2:** the 71 lead-0 rows read `no_cell` with `lead_rule` verbatim at both thresholds, and a shown `baja_socal × d1-2` cell still serves none of them (320 = 20 × 16 served). 292's region, lead_bucket, threshold and k_bin sets, and Neon's deparsed CHECKs, equal `EDGES`'s names.
- **X1 ×2:** the receipts are the routes' statements; no Seq Scan except on tables under 10k rows (sizes stated); every read under 10 ms; the INDEX plan has two whole-key probes and no Join Filter.
- **R1 ×4:** the four bodies.

**Red on main.** At `b399c67`, with this lane's test, loader, fixtures and receipts copied in, collection errors with `ModuleNotFoundError: No module named 'ar_index'`, and both routes return **404** on main's `app`.

**R, the mutation rehearsal** (`tests/rehearse_ar_index_d091766.py`, output in `docs/receipts/ar-index-d091766/reds.txt`): **16 of 16 breaks red**, and the tree was restored byte for byte (`c76a142c5a8c5811 → c76a142c5a8c5811`). The clean tree is green before and after.

| # | Break | Red |
|---|---|---|
| 1 | a probability served for an unscored cell | A2/A3 |
| 2 | `n250` served for an unscored cell | A2 |
| 3 | `DISTINCT ON (level_set)` for the newest init | A5 |
| 4 | a band's top half-degree derived into the band above | A4b |
| 5 | not-shown served as shown | A3 |
| 6 | unscored counts as shown | A2 |
| 7 | lead 0 put in d1-2 | A6 |
| 8 | `no_cell` loses the rule | A6 |
| 9 | the pin not checked | A4 |
| 10 | the pin mistyped | A4 |
| 11 | a disagreeing point silently picked | A4b |
| 12 | no statement timeout | A5 |
| 13 | memo removed | A5 |
| 14 | the coast_ivt LATERAL without `LIMIT 1` | A5 |
| 15 | the odds URL served | A1 |
| 16 | `ctrl` withheld with the counts | A2 |

**The whole suite, with counts:** SUITE_COUNTS

## 7. What the spec got wrong

1. **`withheld` cannot be one object per (point, lead).** A cell includes the threshold, so on one row `p250` can show while `p500` cannot, and the two can be withheld for different reasons. `withheld` is therefore keyed by threshold (`{"250": {reason…}, "500": {…}}`), and only the withheld thresholds are present.
2. **"In the route, a mismatch is a 500 that names the point" has nothing to compare against at runtime.** The route reads no object (the index rule, and the Tropics precedent), so it never sees the `coast_ivt` object's regions. The cross-check against the object is A4b, at test time, for 71 of 71. In the route, the 500 covers the failure it *can* see: a point whose rows carry two positions, or a lat other than the one its `point_code` names. Either would make "one region per point" a silent pick.
3. **The band-edge clause is vacuous on today's coast list.** The gaps between bands (34.5–35.0, 42.5–43.0, …) contain no 0.5° latitude, and each band's top (34.5, 42.5, 49.5, 55.5) is inside its own closed interval. "The half-degree belongs to the band below" therefore assigns nothing beyond what `[lat_min, lat_max]` already does. What can go wrong is the closedness itself: a half-open band drops 34.5 to no region, and an off-by-half-degree band moves it to the band above. Mutation 4 is the second case, and A4b pins all eight edge points by name.
4. **The odds object is public, so the gate is only the API's.** A HEAD of `https://display.energylake.io/ar/gefs/20261017/00Z/odds.sub7_v1.json` at 12:54Z returned **200**. Every cycle's odds object sits at a key `ar_display_cycle_key` makes predictable, and it carries every `n250`/`n500` ungated. The publisher marks it "BANK ARTIFACT, NOT DRAWN AS-IS", the API never serves its URL, and A1 asserts that. But any reader who constructs the URL bypasses D-09-25-245. This lane cannot fix that: it needs a bucket policy, or publishing the odds object somewhere other than the public display bucket. **Flagged for the architect.**
5. **The absence vocabulary is short by two names.** Ruling 1's refusal needs a reason, and here it is `edges_pin_mismatch`. A scored cell that does not show needs a reason to carry its `show_reasons`, and here it is `not_shown` plus `show_reasons` verbatim. The spec's list, "or a scored cell's own show_reasons", leaves the wrapper unnamed.
6. **An exact-key LATERAL is not one (table, key) scan unless it stays a subquery** (§3). The spec's "each LATERAL scans one (table, key)" holds only with the d091551 `LIMIT 1`. Without it, Neon flattened the join and walked `(level_set, kind)` per cycle.
7. **`ar_scores`' newest scoring is a seq scan today.** The table has 0 rows and has never been analyzed. The PK leads with `scored_at`, so the backwards `LIMIT 1` becomes an index read once the table has rows and statistics. That is under 10k rows either way, and it is re-checkable when the backtest writes.
8. **`/landfall` reads the whole of `ar_landfall_latest` by Seq Scan** (6,603 rows, 5.5 ms). That is within X1's rule, because the whole table is the answer. A second level set would double the table and turn this into a filter scan: at that point the read should be pinned to the PK's `level_set` prefix and re-receipted.
9. **`?init=` can only ever name the newest cycle.** `ar_landfall_latest` keeps the newest only, so any other init is `no_init` (a 200 with the held cycle named), never an older cycle's odds. The index lists 16 inits, but `/landfall` can serve one of them.

## 8. Not done

- Nothing from the spec is left undone.
- **No real `ar_scores` rows** exist to bank as A3's vector; the constructed fixture stands until the backtest's score mode writes rows (§5).
- §7.4, the public odds object, is reported and not fixed: it is outside this repo.
