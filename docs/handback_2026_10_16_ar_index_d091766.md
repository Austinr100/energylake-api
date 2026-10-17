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

## 0.8 Re-fire 2026-10-17 12:04Z — STOP-D fires again: the table is present and **empty**

**One line:** `ar_display_objects` now exists (migration 324 committed 10:35:28Z, exactly as the architect said) but holds **0 rows of any kind**, so there is no `coast_ivt` row, so the lane still does not open. Nothing was built. This is the spec's own gate behaving correctly, not a new obstacle.

**Why:** the publish step was *installed* at 10:49Z; it has not *run*. The first `ar-gefs-points` fire since installation is today's 18:52Z, and this read is 12:04:54Z — about six and three-quarter hours early. `ar_runs` is still 16 rows with newest `init_ts = 2026-10-17T00:00:00Z`, unchanged from §0.2, which confirms the lane's own reading: no cycle has fired since §0, so the publish has had no occasion to emit.

### 0.8.1 STOP-D receipt (read 2026-10-17T12:04:22.486Z and 12:04:54.030Z, `transaction_read_only = on` read back in both, PostgreSQL 17.11)

| Check | §0 (10:32Z) | Re-fire (12:04Z) |
|---|---|---|
| `to_regclass('public.ar_display_objects')` | NULL — absent | **present** |
| rows with `kind = 'coast_ivt'` | n/a (no table) | **0** |
| rows of *any* kind | n/a | **0** |
| `ar_truth_days` | absent | present, **0 rows** |
| `max(version)` in `schema_migrations` | 323 | **328** |
| `ar_runs` | 16, newest `2026-10-17 00Z` | 16, newest `2026-10-17 00Z` — unchanged |
| `ar_landfall_latest` | 6,603 (71 pts × 93 steps) | 6,603 (71 pts × 93 steps) — unchanged |
| `ar_scores` | 0 | **0** — still unscored |

Migrations applied since §0: `324_ar_display_objects_and_truth_witness.sql` (10:35:28Z), `325_clock_promotions_d091778.sql` (10:36:30Z), `326_clock_arms_d091778.sql` (10:37:25Z), `328_tropical_verification_clock.sql` (10:38:11Z) — all `status = committed`. **No row for 327 at any status**; that is another lane's business, recorded here only because it sits inside the range this lane read.

Because `ar_scores` is still empty, ruling 3's note stands unchanged and the architect's instruction on A3 applies as written: when the backtest's score mode writes the first `ar_scores` rows, those become A3's vector instead of a fixture.

### 0.8.2 What the re-fire learned that §0 could not: the table's shape

§0 was blind to this table — it did not exist. It does now, and its DDL settles two things the rulings had to assume. Recorded so the next firing does not have to re-derive them.

Columns: `init_ts` (nullable), `level_set`, `kind`, `display_key`, `sha256`, `bytes`, `published_at default now()`.

**Ruling 2's mechanics are sound.** `display_key` and `sha256` both exist and are `NOT NULL`, so "fetch `https://display.energylake.io/<display_key>` and sha-check it against `ar_display_objects.sha256`" is executable as written. Better: the key is not free text — a CHECK derives it, so the next firing can compute it rather than trust it.

- cycle objects — `ar_display_cycle_key`: `display_key = 'ar/gefs/' || YYYYMMDD || '/' || HH || 'Z/' || kind || '.' || level_set || '.json'` (init_ts at UTC)
- registry — `ar_display_registry_key`: `display_key ~ '^ar/registry/points_v[0-9]+\.[0-9a-f]{8}\.json$'` **and** its 8 hex digits equal `left(sha256, 8)`

**Three kinds, not one.** `ar_display_objects_kind_check`: `coast_ivt | odds | registry`. The spec's `/index` shape ("`inits` to list, `registry` to name") maps onto this directly, and `ar_display_cycle_or_registry` makes the split exact: `(kind = 'registry') = (init_ts IS NULL)`. So `inits` is built from the non-registry rows and `registry` is the cycle-less row — the route must not expect an `init_ts` on it, and must not expect a registry row to appear under any cycle.

Other terms: `bytes > 0`; `sha256 ~ '^[0-9a-f]{64}$'`; `level_set ~ '^[a-z0-9]+_v[0-9]+$'`; FK `(init_ts, level_set) → ar_runs(init_ts, level_set)`, so a published cycle is always an already-known run and `/index` never needs to defend against a cycle `ar_runs` has not got.

`ar_truth_days` is keyed `(valid_date, level_set)` with `r2_key`, `sha256`, `status`, `written_at` — no `init_ts`. It is the truth witness, not a display object, and nothing in this lane's two routes reads it.

**One thing for X1 to state plainly.** The only index on `ar_display_objects` is the primary key, `(level_set, kind, display_key)` — `init_ts` is **not** indexed and not in the PK. Any "newest cycle" read therefore has no supporting index and will sort or scan. At 32 kB that is free and the CLAUDE.md d091551 `LATERAL` form is not in question here, but X1 should say so with the table size next to it rather than leave a reader to assume an index that is not there. Note also that uniqueness is carried by `display_key`, which encodes the cycle — not by `init_ts` — so two kinds for one cycle are two rows, as intended.

### 0.8.3 Scope of the re-fire

Identical to §0's, and for the same reason. **No Neon write** — both statement groups ran in a transaction opened `SET TRANSACTION READ ONLY` with `transaction_read_only` read back `on` before any read. **No R2 read** and **no `display.energylake.io` call** — ruling 2's test-vector fetch is the first thing the next firing does, and it needs a published object to fetch. **No route, no test, no constant, no fixture written**; the edges pin of ruling 1 is not yet in the tree. The only change in this commit is this section.

### 0.8.4 What unblocks the next firing

Unchanged from §0.1 except that its first two steps are now done. What remains is one step: **`scripts/ar_publish.py --backfill --emit` must run.** Today's 18:52Z `ar-gefs-points` fire does it, or an earlier dispatch on the captain's word. Re-fire this paste after that; it re-checks STOP-D itself, and the condition it checks — at least one `coast_ivt` row — is the right one and needs no change.

### 0.8.5 What this spec got wrong (re-fire addendum to §0.6)

7. **STOP-D's premise was one step short in the other direction, too.** §0.6 ¶1 said the binding constraint was migration 324, one step before the publish. Both halves are now visible: 324 *and* the publish are separate gates, and clearing the schema gate moved nothing, because the table a migration creates is empty until a job fills it. The condition the architect re-worded for this firing — "`ar_display_objects` holds at least one `coast_ivt` row" — is correct precisely because it tests the *data*, not the schema and not the install time. A firing gated on "324 applied" or "the publish step installed" would have opened this lane at 10:49Z onto an empty table.
8. **`kind` is not implicitly `coast_ivt`.** The spec and the rulings speak of "the published `coast_ivt` object" as though it were the table's only tenant. The CHECK admits `odds` and `registry` as well, and the registry row is structurally different (no `init_ts`). A route written against the assumption that every row carries a cycle will fail on the registry row the moment the publish emits one — which is the same fire that opens this lane.
