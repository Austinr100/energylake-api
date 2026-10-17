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

