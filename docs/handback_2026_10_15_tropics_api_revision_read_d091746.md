# Handback d091746: the Tropics index reads `tropical_display_in_force` by advisory, on banked vectors

**Lane:** d091746 · **Repo:** energylake-api · **Branch:** `claude/tropics-display-revision-read-c2gr2r`, from main at `a7af1b0` (#116 merged).
**Ran:** 2026-10-15, 21:50Z–22:40Z (container clock). **Environment:** EL 6.
**Rulings held:** D-09-25-231 · D-09-25-230 · D-09-05-T · D-09-25-226 §2/§5 · D-09-25-75 / D-09-25-76 (CLAUDE.md).

**Scope kept:**
- Branch only. No PR, no merge, no deploy.
- **No Neon write.** Five connector calls, each ONE transaction opened by `SET TRANSACTION READ ONLY`, with `transaction_read_only = on` read back in the results. Every statement was a SELECT or an `EXPLAIN (ANALYZE, BUFFERS)` of one. No connection was held across other work: the connector opens one per call.
- **No R2 read.**
- **Network:** GitHub only — energylake-pantry `main` at `5dc57d1`, read-only (`migrations/314`, `316`, `tropical/publish.py`).
- **Out of scope, untouched:** the publisher, the dashboard, any index on Neon, `uom`/attribution.

---

## 0. In ten lines

1. **The display read is now 314's own reader.** `DISPLAY_FILES_SQL` reads `tropical_display_in_force` by `product = ANY(…) AND storm_id = … AND advisory_or_cycle = …`.
   - `revision` comes from the view; `corrected` is `revision > 0`.
   - The original is the `revision = 0` row at the same identity: one LATERAL probe, only where `revision > 0`.
   - The same applies to WSP (`storm_id ''`) and to `/members` manifests (`DISPLAY_MEMBERS_SQL`).
2. **NHC's files and the member parquets stay on 294's view.** They are read by exact key, with the source as a SQL literal (`'nhc'`, `'parsed'`). No display row is read through 294's view any more. Tests sweep this both statically and over every statement the routes send.
3. **All twelve real corrections are served as their `r1`**, with `revision 1`, `corrected true` and the original stated.
   - Ten have the same bytes as their revision-0 row; `original` is stated for them anyway.
   - Isaias 016 TCD/TCP differ: same size, new sha.
4. **`/text` reads what the publisher writes.**
   - `sections` = `[{kind}]` from `section_kinds`.
   - `title` and `n_forecast_rows` are served.
   - `sections_rule` says the titles are in the object at `url`, not in the ledger.
   - `/text` gains a top-level `revision`/`corrected` (the max over its products) and a `revision_rule`, as `/products` has.
5. **The constructed display layer is retired.** 61 display rows were read verbatim off Neon (22:00:14.622Z), with Neon's sha re-checked on every load. `display_rows.json` is deleted. `make_display_rows.py` survives only for the three member manifests, because no `display:members:*` row exists live.
6. **Migration 316 is loaded verbatim into the test cluster** after 314. Its own pre- and post-checks run there, and its view md5 equals Neon's (`977c97d6…`).
7. **Plans: no seq scan anywhere. The view reaches `tfv_display_immutable` for `(product, storm_id)`, but the advisory match is a Filter** (§3).
   - A WSP read walks all 162 WSP display rows to serve 6.
   - **An expression index is owed to pantry, not urgent** (§4).
8. **Tests:**
   - New `tests/test_tropics_revision_read_d091746.py`: 39 tests.
   - d091741's module: 87 → 98, with the two xfails turned into the real I2 vectors.
   - **Red on main:** 44 of 137 across the two modules.
   - **Green on the branch:** 137 of 137.
9. **Rehearsal: 11 of 11 breaks red.** The brief's "revision DESC dropped" is red only through 316's own post-check and the ddl pin. **On the banked rows I2 cannot see it** (§6.2); a diagnostic break shows that.
10. **The `054` body** the local route serves is in §5 and `docs/receipts/tropics-api-revision-read-d091746/bodies/`, with the banked rows each object came from.

---

## 1. The two SQL texts (and the two that changed beside them)

`tropics_index.py`. The routes run all of them in one transaction after `SET LOCAL statement_timeout` (D-09-25-75), unchanged.

### `DISPLAY_FILES_SQL` (new)

```sql
SELECT 'display' AS source, v.product, v.advisory_or_cycle, v.vintage_key, v.revision,
       v.revision > 0 AS corrected, v.r2_key, v.sha256, v.byte_size, v.issued_ts,
       v.published_ts, v.meta,
       o.r2_key AS original_r2_key, o.sha256 AS original_sha256,
       o.byte_size AS original_bytes
  FROM tropical_display_in_force AS v
  LEFT JOIN LATERAL (
        SELECT a.r2_key, a.sha256, a.byte_size
          FROM tropical_file_vintage AS a
         WHERE v.revision > 0
           AND a.source = 'display' AND a.product = v.product
           AND a.storm_id = v.storm_id AND a.vintage_key = v.advisory_or_cycle
           AND a.status = 'banked') AS o ON true
 WHERE v.product = ANY (%(products)s::text[]) AND v.storm_id = %(storm_id)s
   AND v.advisory_or_cycle = %(advisory_or_cycle)s
 ORDER BY v.product
```

- **Parameters:** `display_files_params(storm_id, advisory_or_cycle, pairs)`. For WSP, `storm_id = ''` and the cycle.
- **The original's probe** is one exact `tfv_display_immutable` key. That index is unique per `(product, storm_id, vintage_key) WHERE source = 'display'`, so there is no ORDER/LIMIT.

### `FILES_SQL` (changed: NHC's files only)

```sql
SELECT v.source, v.product, v.vintage_key, v.r2_key, v.sha256, v.byte_size,
       v.issued_ts, v.fetch_ts, v.meta, v.revision, v.corrected
  FROM tropical_file_vintage_in_force AS v
 WHERE v.source = 'nhc' AND v.product = ANY (%(products)s::text[])
   AND v.storm_id = %(storm_id)s AND v.vintage_key = %(vintage_key)s
 ORDER BY v.product
```

### `DISPLAY_MEMBERS_SQL` (new)

```sql
SELECT 'display' AS source, v.product, v.advisory_or_cycle, v.vintage_key, v.revision,
       v.revision > 0 AS corrected, v.r2_key, v.sha256, v.byte_size, v.issued_ts,
       v.published_ts, v.meta
  FROM tropical_display_in_force AS v
 WHERE v.product = ANY (%(products)s::text[]) AND v.storm_id = %(storm_id)s
   AND (%(cycle)s::text IS NULL OR v.advisory_or_cycle = %(cycle)s)
 ORDER BY v.advisory_or_cycle, v.product
```

### `MEMBERS_SQL` (changed)

It is now `v.source = 'parsed'` only: the member parquets, as before.

### `main.py`

The tropics index section only:
- `_tropics_advisory_reads` runs `DISPLAY_FILES_SQL` after each `FILES_SQL` (the advisory, then the WSP cycle) and concatenates the rows.
- `_tropics_members_build` runs `DISPLAY_MEMBERS_SQL` after `MEMBERS_SQL`.
- The `/text` docstring and the section comment are updated.

d091673's "main.py only gains lines since its base" still holds: the edited lines are d091741's, not the base's.

**Dropped:** `FILES_SQL`'s LATERAL for NHC originals. No body field reads an NHC file's original; the absences and `source_in_force` read only the copy in force's key and sha.

---

## 2. The bank: what was read, when, and the counts

**Script:** `tests/fixtures/tropics_d091741/bank_display_from_neon.py`.
- It holds the statements verbatim; `--sql` prints them as sent.
- It writes `bank/*.jsonl.gz` only after each body matches the sha-256 and row count Neon computed in the same transaction.
- The read stamp and counts go into `manifest.json` → `"d091746"`. Suite test V2 asserts them, and `rows()` re-checks every sha on every load.

**Read 2026-10-15T22:00:14.622Z**, one read-only transaction:

| kind | rows | sha256 (Neon's) | what |
|---|---:|---|---|
| `display` | **61** | `7073a131…7a483` | every display row, every revision, of 49 identities (51 asked; Simon 033 has no `ww` and no `surge_ww`) |
| `ledger_033` | 8 | `48e60d8b…3a5973` | Simon 033's `cone_kmz`, `tcd`, `tcp` and 3 CurrentStorms entries; the two WSP zips of 2026101512 |
| `official_033` | 8 | `495c6b10…25b220` | Simon 033's official points (init 2026-10-15 12Z) |
| `dataset_two` | 1 | `10c3bac4…59da3a` | `nhc_two_vintage` (316's probe inserts under it; FK) |
| `migrations` | 2 | `2ecd3d53…dcf214` | `schema_migrations` 314 and 315 (316's pre-check requires both) |

**The ledger at the read:**
- 8,286 rows in total, 710 of them `display`.
- 20 display rows are `r<n>`: the 12 the brief names, plus 8 TOA rows (`toa_earliest`/`toa_mostlikely` at ep182026 054, 056, 057 and ep212026 001; not served by these routes).
- Max migration 316; view md5 `977c97d66d70ff8a8ed7e6992545f6c7`.

**The 49 identities:**
- The ten advisory objects d091741 constructed. Receipt: they still equal the recon's parse byte for byte; I1 and T1 assert it.
- The WSP bands and grids of 2026101112 and 2026101506.
- The twelve corrections' identities.
- Simon 033 (T2).
- WSP 2026101512 (I3) and 2026101012 (Rachel 054's cycle, so the 054 body is complete).

**Two changes this forced in d091741's assertions:**
- **The WSP band rows changed** sha and size (d091740 compacted them). They are re-pinned to the banked rows, with an explicit assertion that they are NOT `recorded_k1`'s parse.
- **2026101506's grids are published live.** d091741 asserted them `not_published` on the constructed rows; they are now served.

**The test cluster load order** (`tests/load_bank_d091741.py`):
1. 291 tables, 294 and 297 views, 314's change (the hotfix's `ddl_314.sql`).
2. The datasets (+ `nhc_two_vintage`).
3. `schema_migrations` (`ddl_316_ledger.sql`: Neon's seven columns, read from `information_schema`), then its 314/315 rows.
4. **`ddl_316.sql` verbatim.** Its sha `0eca8990…` is pinned to pantry main `5dc57d1`; its pre-checks (a)–(e), its rolled-back probe and its post-checks (1)–(4) all run.
5. The 13:50Z slice, Simon 033's rows, then the 61 display rows, so every display row meets 316's CHECK, `tfv_display_immutable` and the 314 CHECKs.

**Retired and kept:**
- `display_rows.json` (d091741's 19 constructed rows) is **deleted**.
- `make_display_rows.py` is **kept, cut to the three member manifests.** No `display:members:*` row is published (d091743 has not run), so `/members`' served and `too_few_members` cases can only still be made.
- `test_F_the_display_rows_are_the_generators` is now `test_F_the_constructed_manifests_are_the_generators`.

---

## 3. The plans (Neon, `EXPLAIN (ANALYZE, BUFFERS)`, 2026-10-15T22:04:52Z)

**Receipts:**
- `docs/receipts/tropics-api-revision-read-d091746/neon_plans.txt` (verbatim).
- `explains.json` (psycopg's mogrify of the routes' own statements and parameter builders: `explains_sql.py`).

**The ledger at the time:** 710 display rows, of which 90 `display:cone`, 148 `display:tcd`+`display:tcp` and 162 `display:wsp*`.

| statement (params) | how the view's rows are reached | rows walked → kept | buffers | exec |
|---|---|---:|---:|---:|
| DISPLAY_FILES_SQL products ep182026 054 | Index Scan **tfv_display_immutable**, Index Cond `product = ANY, storm_id`; **Filter** `substring(vintage_key …) = '054'` | 43 → 4 (39 removed) → 2 in force | 11 + 6 (original) hit | 0.193 ms |
| DISPLAY_FILES_SQL text al092026 016 | the same | 25 → 4 (21 removed) → 2 | 7 + 6 hit | 0.165 ms |
| DISPLAY_FILES_SQL text ep202026 033 | the same | 74 → 2 (72 removed) → 2 | 21 + 6 hit | 0.221 ms |
| DISPLAY_FILES_SQL wsp 2026101012 | **Bitmap** Index Scan **tfv_display_immutable** `(product = ANY six, storm_id = '')`, Filter the cycle | **162 → 6 (156 removed)** | 37 + 18 hit, 33 heap blocks | 0.534 ms |
| DISPLAY_FILES_SQL wsp 2026101512 | the same | 162 → 6 (156 removed) | 37 + 18 hit | 0.501 ms |
| DISPLAY_MEMBERS_SQL ep202026 all / 2026101112 | Index Scan **tfv_identity** `(source 'display', product = ANY, storm_id)`; the cycle a Filter | 0 (none published) | 2 hit | 0.04 ms |
| FILES_SQL (products 054, wsp 2026101012, text 016) | Index Scan **tfv_identity**, all four columns | 2–4 | 3–10 hit | ≤ 0.09 ms |
| MEMBERS_SQL ep202026 all | Index Scan **tfv_identity** | 91 | 60 hit, 38 read (cold) | 11.5 ms |
| ENTRY_NEWEST_SQL ep202026 (unchanged) | Index Scan **tfv_identity** | 84 | 17 hit, 61 read (cold) | 9.5 ms |

- **The corrected original's probe** is `Index Scan using tfv_display_immutable on tropical_file_vintage a`, Index Cond on all three key columns: one exact key per corrected object. It never runs for a revision-0 row: Join Filter `revision > 0`, "Rows Removed by Join Filter: 6" on the WSP reads.
- **No `Seq Scan` on either table, anywhere.**
- **The two cold reads** (9–12 ms, buffers read from storage) are reads this lane did not change. d091741's X1 bound of 5 ms holds for warm plans only, so X1 now allows 20 ms where buffers were read.

**So, measured:** the planner does reach the ledger through an index, via the view's DISTINCT ON. But the view's `advisory_or_cycle` is `substring(vintage_key …)`, so the advisory is matched by a Filter after a range on `(product, storm_id)`.
- **Advisory products:** that range is every display row of the storm's products: bounded by a storm's life (Rachel's 58 advisories × 3 products, ≈ 180 rows at most).
- **WSP:** `storm_id = ''` makes it **every WSP display row ever published**. It is 162 today and grows by 6 per cycle, 4 cycles a day.

## 4. The index verdict: owed (pantry's lane to add), not urgent

- **Today:** ≤ 0.53 ms and ≤ 55 shared buffers per read, behind a 300 s single-flight memo per (storm, advisory).
- **At the season's end** (~180 days × 4 cycles × 6 products ≈ 4,300 WSP rows), every `/products` build walks them all: on today's density (33 heap blocks per 162 rows) that is ~900 blocks for 6 rows.

The fix is an expression index whose expression is **the view's own, as 316 left it**:

```sql
CREATE INDEX CONCURRENTLY tfv_display_base
    ON tropical_file_vintage (product, storm_id,
                              (substring(vintage_key FROM '^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})')))
 WHERE source = 'display';
```

- **The pattern must be 316's, not 314's.** The brief's proposal used 314's `'^([0-9]{3}[A-Z]?|[0-9]{10})'`, which the planner would never match against the view's expression after 316.
- **Status:** not added here, and not tested on Neon. Pantry should EXPLAIN the WSP read with it before and after; the expected plan is an Index Cond on all three columns, 6 rows.

---

## 5. The live receipt: `/products?storm_id=ep182026&advisory=054`

**File:** `docs/receipts/tropics-api-revision-read-d091746/bodies/products_ep182026_054.json`, as the local route serves it over the bank, minus the `cache` block (the memo's state at serve time).
- Its compact form (`json.dumps(separators=(",", ":"), ensure_ascii=False)`) has sha256 `974aa3953aacff04195783c8d60d827ce544b0d9cc7f2b312577748714481fbe`.
- Test R1 re-serves it and compares.

**Served from these banked rows** (`bodies/served_from.json`):

| object | served row (vintage_id, key) | sha256 | original row |
|---|---|---|---|
| cone | 7338 `054r1` | `63baad562bc2…f294e087` | 7337 `054`, same sha |
| ww | 7721 `054r1` | `e90f66c33582…20cd5517` | 7720 `054`, same sha |
| wsp34/50/64_bands | 7568 / 7569 / 7570 `2026101012` | `c18bc75b…`, `0bf99a51…`, `94f61a7a…` | — |
| wsp34/50/64_grid | 7643 / 7644 / 7645 `2026101012` | `89d2850a…`, `28055731…`, `32b7c3d6…` | — |

**What else drives the body:**
- **The NHC side** is the 13:50Z slice:
  - entry `054:4b6fae86` (fetched 2026-10-10T15:32:23Z);
  - the corrected `cone_kmz__b0b5d20f.kmz` and `ww_kmz__a3a146da.kmz`, which are 294's copies in force, hence `source_in_force: true`;
  - official 054 (init 12Z), hence cycle 2026101012.
- Nothing about 054 has changed on Neon since: no newer entry, no `r2`.
- **On Railway after deploy,** the same path should serve this body with only `cache` differing. The objects should match sha for sha, since every row above is Neon's own.

```json
{"storm_id":"ep182026","name":"Rachel","basin":"ep","advisory":"054","advisory_requested":true,"init_ts":"2026-10-10T12:00:00+00:00","position_valid_ts":"2026-10-10T15:00:00+00:00","issued_ts":"2026-10-10T15:00:00+00:00","nhc_entry":{"vintage_key":"054:4b6fae86","issued_ts":"2026-10-10T15:00:00+00:00","fetch_ts":"2026-10-10T15:32:23.596695+00:00","classification":"TS","refs":{"pws":"054","tcd":"054","tcm":"054","tcp":"054","ww_kmz":"054","cone_kmz":"054","track_kmz":"054","fcst_5day_zip":"054","fcst_radii_zip":"054","initialradii_kmz":"054","toa_earliest_kmz":"054","forecastradii_kmz":"054","toa_mostlikely_kmz":"054"}},"nhc_entry_absence":null,"revision":1,"corrected":true,"objects":[{"product":"cone","url":"https://display.energylake.io/tropics/ep182026/adv054r1/cone.geojson","sha256":"63baad562bc2698131bc6aa56d58b7a335b0c5e5232e3785adaa10f2f294e087","bytes":28736,"source_r2_key":"tropical/nhc/2026/ep182026/adv054/cone_kmz__b0b5d20f.kmz","source_sha256":"b0b5d20f101a81ecf783342e0ff8904fb7380f44d17381d89063a7a31a989e8f","geometry_types":{"Polygon":1},"vertices":1296,"revision":1,"corrected":true,"original":{"url":"https://display.energylake.io/tropics/ep182026/adv054/cone.geojson","sha256":"63baad562bc2698131bc6aa56d58b7a335b0c5e5232e3785adaa10f2f294e087","bytes":28736,"absence":null},"source_in_force":true},{"product":"ww","url":"https://display.energylake.io/tropics/ep182026/adv054r1/ww.geojson","sha256":"e90f66c33582b60301de6ad112d864ff8c997706ab0cf9345b1ddbcd20cd5517","bytes":2493,"source_r2_key":"tropical/nhc/2026/ep182026/adv054/ww_kmz__a3a146da.kmz","source_sha256":"a3a146da4f8e04fe5bbbc5645a96d4ae1b0a37bb36ccf1c9a57c3bc12fbdf4a4","geometry_types":{"LineString":5},"vertices":17,"revision":1,"corrected":true,"original":{"url":"https://display.energylake.io/tropics/ep182026/adv054/ww.geojson","sha256":"e90f66c33582b60301de6ad112d864ff8c997706ab0cf9345b1ddbcd20cd5517","bytes":2493,"absence":null},"source_in_force":true}],"absent":[{"product":"surge_ww","reason":"basin_has_none","detail":"NHC issues storm surge watches and warnings for the US Atlantic and Gulf coasts only; this storm's basin is 'ep'"}],"wsp":{"cycle":"2026101012","synoptic_ts":"2026-10-10T12:00:00+00:00","objects":[{"product":"wsp34_bands","url":"https://display.energylake.io/tropics/wsp/2026101012/wsp34_bands.geojson","sha256":"c18bc75b6bc8b81ce2c125a9648a0eec99a7044a03adb6226d00a3fd66838efc","bytes":768836,"source_r2_key":"tropical/nhc/wsp/2026101012/wsp_5km_zip.zip","source_sha256":"da4eee80d6131cd291f767e56d48cb505b9872bff5e19239fdd58a2cfabe7a44","geometry_types":{"MultiPolygon":11},"vertices":43173,"revision":0,"corrected":false,"original":null,"source_in_force":true},{"product":"wsp50_bands","url":"https://display.energylake.io/tropics/wsp/2026101012/wsp50_bands.geojson","sha256":"0bf99a5177be769855d71f145ea0f42326d30ce6e2e27505f53dc0c5a4e505c4","bytes":420781,"source_r2_key":"tropical/nhc/wsp/2026101012/wsp_5km_zip.zip","source_sha256":"da4eee80d6131cd291f767e56d48cb505b9872bff5e19239fdd58a2cfabe7a44","geometry_types":{"MultiPolygon":11},"vertices":23533,"revision":0,"corrected":false,"original":null,"source_in_force":true},{"product":"wsp64_bands","url":"https://display.energylake.io/tropics/wsp/2026101012/wsp64_bands.geojson","sha256":"94f61a7ac4364d09600792e13369e41cb161d60049ac35fd9990073b4a3d00b0","bytes":218452,"source_r2_key":"tropical/nhc/wsp/2026101012/wsp_5km_zip.zip","source_sha256":"da4eee80d6131cd291f767e56d48cb505b9872bff5e19239fdd58a2cfabe7a44","geometry_types":{"MultiPolygon":11},"vertices":12156,"revision":0,"corrected":false,"original":null,"source_in_force":true},{"product":"wsp34_grid","url":"https://display.energylake.io/tropics/wsp/2026101012/wsp34_grid.geojson","sha256":"89d2850a81569db652d248891508574dc86f13ed8c0eecae04325ee2cb3b3ac0","bytes":358166,"source_r2_key":"tropical/nhc/wsp/2026101012/wsp_halfdeg_zip.zip","source_sha256":"718a0dbae0364dfafd7bd79828a7eeb282bc03437e2c3e2973c19a903f34c6cd","geometry_types":{"Point":3152},"vertices":3152,"revision":0,"corrected":false,"original":null,"source_in_force":true},{"product":"wsp50_grid","url":"https://display.energylake.io/tropics/wsp/2026101012/wsp50_grid.geojson","sha256":"28055731a6a300e72db96ea8de3916ce35d02fb6a48dab4645169a818f3cb244","bytes":200216,"source_r2_key":"tropical/nhc/wsp/2026101012/wsp_halfdeg_zip.zip","source_sha256":"718a0dbae0364dfafd7bd79828a7eeb282bc03437e2c3e2973c19a903f34c6cd","geometry_types":{"Point":1762},"vertices":1762,"revision":0,"corrected":false,"original":null,"source_in_force":true},{"product":"wsp64_grid","url":"https://display.energylake.io/tropics/wsp/2026101012/wsp64_grid.geojson","sha256":"32b7c3d67d22135c7c5dd57dadbcebf9d19839644f191fe0612e44996ad2c002","bytes":142449,"source_r2_key":"tropical/nhc/wsp/2026101012/wsp_halfdeg_zip.zip","source_sha256":"718a0dbae0364dfafd7bd79828a7eeb282bc03437e2c3e2973c19a903f34c6cd","geometry_types":{"Point":1253},"vertices":1253,"revision":0,"corrected":false,"original":null,"source_in_force":true}],"absent":[],"absence":null},"absence":null,"advisory_rule":"Without `advisory`, NHC's newest advisory for the storm: the one its newest CurrentStorms entry is of (issued_ts, then fetch), so a text-only post-tropical advisory counts; for a storm with no entry, the newest official forecast (newest init_ts, then position valid time, never the string). Per product, the copy in force (published objects: pantry 314's tropical_display_in_force; NHC's files: 294). init_ts is the advisory's synoptic time, from its official forecast; null where NHC issued none.","wsp_rule":"WSP is issued per synoptic cycle, basin-wide, not per storm: the cycle is the advisory's synoptic time (its official forecast's init_ts) as YYYYMMDDHH. Bands are the 5 km zip's contours (11 percentage bands); the grid is the half-degree zip's points, value in percent, cells below 0.1 % absent.","revision_rule":"revision and corrected are each object's own: the copy in force is the highest revision published for the advisory or cycle (pantry 314's tropical_display_in_force; a correction NHC issued is its own ledger row, key '<ADV>r<n>'), corrected = revision > 0, and `original` is the revision-0 object where one is corrected, stated even when its bytes equal the correction's (NHC re-posted the same file). The top-level revision is the highest served; source_in_force says whether the NHC file an object names is the copy in force now.","index_rule":"Each object is a vintage-ledger row of the display bucket, the copy in force per (product, storm_id, advisory or cycle) as pantry 314's tropical_display_in_force names it (source 'display', product 'display:<name>', r2_key = the display key), served as the ledger states it: url = https://display.energylake.io/<r2_key>. The API reads no R2 and fetches no object. The contract is the publisher's (pantry tropical/publish.py, migrations 314 and 316).","reasons":{"not_issued":"NHC issued none: its CurrentStorms entry for this advisory links no such product, or links an earlier advisory's file","basin_has_none":"NHC issues storm surge watches and warnings for the US Atlantic and Gulf coasts only","not_banked":"the bank holds no copy of the NHC file this would be published from","not_published":"the NHC file is banked; no published display object is in the ledger"},"timestamps":"ISO 8601, UTC, e.g. 2026-10-09T12:00:00+00:00","not_served":{"object_bytes":"This API reads no R2 and serves no object bytes: every url is the display bucket's, and the browser fetches it.","alerts":"NWS tropical watches and warnings (api.weather.gov) have no route here: the page fetches them directly (recon d091737 §5)."},"nhc_terms":"NOAA/NHC products are public domain. They are served as the bank parsed them, are not presented as NHC's own official display, and imply no endorsement."}
```

**Main, today, serves this path differently:**
- `cone` and `ww` at `…/adv054/…` with `revision 0`, `corrected false`, `original null`.
- `source_in_force: false`: the r0 row names the pre-correction `cone_kmz.kmz` (`819006cd…`), which is no longer 294's copy in force.

**The other bodies in `bodies/`:**
- `text_al092026_016.json`: r1 TCD and TCP, originals with different shas.
- `text_ep202026_033.json` and `products_ep202026_033.json`: T2 and I3.

---

## 6. Tests

### 6.1 The cells

**New module:** `tests/test_tropics_revision_read_d091746.py`, 39 tests.

| cell | asserts | tests |
|---|---|---:|
| V1 | `DISPLAY_FILES_SQL`/`DISPLAY_MEMBERS_SQL` name `tropical_display_in_force` and `advisory_or_cycle`; `FILES_SQL`/`MEMBERS_SQL` and their params carry no display source or product; the static sweep (every statement on 294's view has a literal `nhc`/`parsed` source, none takes a source parameter); the wire sweep over four routes (no statement on 294's view carries `display`; every `display:` product is asked of the 314 view) | 7 |
| V2 | the read stamp (22:00:14.622Z) and count (61) in the fixture; the companions' counts; the identities read and nothing else; the twelve corrections (ten same bytes, two not, same size, `prior_sha256` = the r0's source); 316's sha = pantry's and its checks ran (`schema_migrations` 314/315/316, view md5 = Neon's); the four display CHECKs validated, 316's 12-digit pattern, all 61 rows pass; `tfv_display_immutable` refuses a second object under `054r1` | 4 |
| I2 | each of the twelve corrections served as its r1 (url, sha, bytes), `revision 1`, `corrected`, original stated, and the body's `revision`/`corrected`; an uncorrected object states no original | 13 |
| T2 | Simon 033 `/text`, named and default: `sections` = the bank's `section_kinds` in order (TCD 3, TCP 5), titles, `n_forecast_rows` 9/0, Beven, both times, url/sha/bytes; the sweep (no `meta.get("sections")`, `META_TEXT` names `section_kinds`, no banked row carries `sections`) | 3 |
| I3 | Simon 033: `init_ts` 12Z, cycle 2026101512, six objects at the banked shas, and the WSP display read was asked with `storm_id ''` | 1 |
| X1 | the receipts are the routes' statements; for each display read: `tfv_display_immutable`, the substring match a Filter and never an Index Cond, the rows removed, the original's exact-key probe, hit-only buffers ≤ 60, < 1 ms | 6 |
| R1 | the four handed-back bodies are what the routes serve; the 054 rows are the r1 pair and their cycle | 5 |

**d091741's module (87 → 98):**
- **I2:** the two `xfail(strict)` tests are now real, both routes:
  - `test_I2_the_copy_in_force_is_revision_1_and_the_original_is_stated[products-ep182026-054 | text-al092026-016]`;
  - `test_I2_the_newest_default_agrees_with_the_named_advisory[…]`, on the bank with NHC rewound to 2026-10-10T17:30Z (`lb.AS_OF_054_016`), where 054 and 016 are each storm's newest;
  - `test_I2_the_ledger_holds_both_copies_and_the_view_one`, which also shows 294's view naming the r0: the defect.
- **Re-pins, as §2 says:**
  - WSP bands now pinned to the banked rows;
  - Simon's default is now 033;
  - T1 sections as `[{kind}]` plus title and `n_forecast_rows`;
  - F tests;
  - X1 reads this lane's receipts and admits `tfv_display_immutable`;
  - C names the one bare read (`DISPLAY_FILES_SQL`'s original).

### 6.2 Mutation rehearsal (R)

**Script:** `tests/rehearse_tropics_revision_read_d091746.py` → `reds.txt`.
- Clean before and after: 137 passed.
- Tree sha256 `7bb3a61e…` → `7bb3a61e…`, restored byte for byte.

| # | break | result |
|---|---|---|
| 1 | **`advisory_or_cycle` → `vintage_key`** in `DISPLAY_FILES_SQL`'s WHERE | RED: 16 failed (I2: the r1's key is `054r1`, so `054` finds nothing) |
| 2 | **`revision DESC` dropped** from the view, as loaded (`ddl_316.sql`) | RED: 18 errors. **316's own post-check refuses the view** (md5 ≠ `977c97d6…`), and the ddl pin would too |
| 3 | *diagnostic:* the same view re-made **after** 316 (past its checks and the pin) | **GREEN, as expected**: on every banked correction the r1 has the same `published_ts` and a larger `vintage_id` than its r0, so the view's tiebreak names it anyway. **I2 cannot see this break on real vectors.** |
| 4 | **the fixture's r1 rows deleted** (`054r1`, `016r1`) | RED: 9 failed |
| 5 | the original's probe never runs | RED: 16 |
| 6 | the original stated only where bytes differ | RED: 12 (the ten same-byte corrections) |
| 7 | `/text`'s top-level revision not the max | RED: 4 |
| 8 | `/text` reads `sections` again | RED: 5 |
| 9 | `/text` serves no title | RED: 4 |
| 10 | the WSP display read asks the storm, not `''` | RED: I3 |
| 11 | `FILES_SQL` admits `display` through 294 again | RED: 5 (V1) |
| 12 | `MEMBERS_SQL` admits `display` through 294 again | RED: 3 (V1) |

d091741's own rehearsal (`tests/rehearse_tropics_index_d091741.py`) is that lane's record. Its breaks 3 and 18 name `FILES_SQL` text that no longer exists, so it no longer runs as written; its receipt stands as of `e3cdffe`.

### 6.3 Red on main, green on the branch

`docs/receipts/tropics-api-revision-read-d091746/red_on_main.txt`: both modules, with this branch's loader, fixtures and receipts, run against main's `tropics_index.py` and `main.py` (worktree at `a7af1b0`).
- **On main: 44 failed, 93 passed.**
- **Of this lane's module on main:** 28 red. The 11 green check fixtures and receipts only (V2 ×4, X1 facts ×5, R1's row shape, I2's uncorrected 058, which main already serves rightly).
- **Branch:** 137 of 137.

### 6.4 The whole suite, before and after

SUITE_TABLE

---

## 7. What this brief got wrong

1. **"470 display rows" is now 710** (read 22:00Z). The 12 corrections the brief tables are exactly the 12 non-TOA `r1` rows. There are also 8 TOA `r1` rows (toa_earliest and toa_mostlikely at ep182026 054, 056, 057 and ep212026 001), which these routes do not serve.
2. **"`revision DESC` dropped → I2 red" cannot be true of banked vectors.** Every `r1` the publisher wrote shares its r0's `fetch_ts` (one batch, 15:22:18.959976Z, or 21:17:51Z for the TOA) and has `vintage_id = r0 + 1`, so the view's tiebreak (`fetch_ts DESC, vintage_id DESC`) picks the r1 without the revision order (§6.2 break 3).
   - The break is caught by loading 316 verbatim (its post-check md5s the view) and by the ddl pin, not by I2.
   - Making I2 itself see it needs a vector where an r0 is published after its r1: only a constructed one, which this lane was told to retire.
3. **The proposed index used 314's pattern.** It must be 316's `'^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})'` to match the view's expression (§4).
4. **"Scans every display row of the product"** is not quite what the plan does.
   - It range-scans `tfv_display_immutable` on `(product, storm_id)`, so an advisory read walks one storm's rows of its products (bounded by the storm).
   - The unbounded case is WSP (`storm_id ''`): every cycle ever published, 162 rows today to serve 6.
5. **"The newest-advisory default agree[s]"** has no real vector in the live bank: no corrected advisory is any storm's newest (Rachel 058, Isaias 020, Trudy past 001, Simon 033). The vectors are the bank with NHC rewound to 2026-10-10T17:30Z, when 054 and 016 were each storm's newest. The display rows stay; no row is constructed.
6. **"The banked Simon 033 rows" needed more than the display rows.**
   - d091741's slice is cut at 13:50Z, before 033 (15Z).
   - Without 033's official forecast there is no synoptic time, so I3's cycle cannot exist.
   - The lane banked 033's NHC rows and official points too, so Simon's default in the fixture moved 032 → 033 (one d091741 assertion).
7. **"Load 316 after 314, verbatim" needs two rows the test cluster lacked:**
   - `schema_migrations` with 314 and 315 (pre-check (a));
   - the `nhc_two_vintage` dataset (its probe's FK).
   Both are banked from Neon; the ledger table's DDL is Neon's columns.
8. **I2's "both routes … the body's `revision` is the max over its objects":** `/text` had no top-level `revision`. Added (`revision`, `corrected`, `revision_rule`) so the clause holds on both.
9. **"The original … one LATERAL, as `FILES_SQL` does today for NHC's":** `FILES_SQL`'s NHC original fed no body field (the absences and `source_in_force` read only the copy in force), so it is dropped rather than kept beside the display one.
10. **"Keep `make_display_rows.py` only if something still generates from it":** something does — the three member manifests. No `display:members:*` row exists live. So it is kept, cut to those, and its test re-pins them.
11. **The ten advisory objects do match byte for byte** (I1 and T1 still assert the recon's `parsed_sha256`). 2026101506's WSP grids, which d091741 asserted `not_published`, are published live and served now.
12. **A note for d091757, which builds on `DISPLAY_FILES_SQL`.** Python's `re` takes the FIRST alternative of `^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})`: `'2026101512'` matches as `'202'`. Postgres takes the longest. A Python mirror of the base pattern must strip the `r<n>` suffix instead (this lane's V2 test first did it the wrong way and failed). 316's header notes the Postgres side.

## 8. Files

**Changed:**
- `tropics_index.py`
- `main.py` (tropics index section only)
- `tests/load_bank_d091741.py`
- `tests/test_tropics_index_d091741.py`
- `tests/fixtures/tropics_d091741/{manifest.json, make_display_rows.py, constructed_rows.json}`

**Deleted:** `tests/fixtures/tropics_d091741/display_rows.json`.

**New:**
- `tests/test_tropics_revision_read_d091746.py`, `tests/rehearse_tropics_revision_read_d091746.py`
- `tests/fixtures/tropics_d091741/{bank_display_from_neon.py, ddl_316.sql, ddl_316_ledger.sql, bank/{display,ledger_033,official_033,dataset_two,migrations}.jsonl.gz}`
- `docs/receipts/tropics-api-revision-read-d091746/{explains_sql.py, explains.json, neon_plans.txt, write_bodies.py, bodies/, reds.txt, red_on_main.txt}`
- this handback
