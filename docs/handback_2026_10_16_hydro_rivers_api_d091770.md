# Handback: the API serves the rivers (lane d091770)

**Branch:** `claude/dreamy-maxwell-2bkxjw`, cut from main `b399c67`. **No PR, no merge, no deploy.**
**Writes:** none. Neon was read through the MCP with `SELECT` statements only (project
`fancy-block-96153928`, default branch, `neondb`). Nothing was written to Neon, R2, the
pantry or the dashboard.
**Receipts:** `docs/receipts/hydro-rivers-api-d091770/` (§7).

---

## Corrections (follow-up, 10-17)

A review after the merge read the five bodies against the pantry and the live bank and
found the plumbing correct but **untested** — no cell called a route — and found two
bodies claiming more than they hold. The follow-up branch
`claude/rivers-followup-d091770` carries the cure (route tests, the stated absences, two
more rehearsal mutations); these are the sentences in THIS document that were wrong.
Every figure below was measured 2026-10-17 on live Neon, read-only, through the MCP
(project `fancy-block-96153928`, `neondb`), after this handback was written.

### C1 — §1's "15 banked+published walks" is **12 walks and 3 crosswalks**

`geo_layer_objects` holds 15 distinct `layer_code`s with a derived object, and 15 is the
count of *layers*, not of walks: **12 are reach walks** (`nhdplus_hr_*`) and **3 are NWM
crosswalks** (`nwm_crosswalk_*`). `/index` serves one entry per published **walk**, so
today it serves **12**, of which **3 carry a crosswalk** (Columbia, Snake, Sacramento).
§0's "the published reach objects (15 rows)" miscounts the same way: at that cut
`geo_display_objects` held 15 publish rows across both kinds, 12 of them reach walks.

The 12 walks are **nine Willamette-basin walks** —
`blue_river_blu`, `coast_fork_cot`, `fall_creek_fal`, `long_tom_frn`,
`north_santiam_det`, `row_river_dor`, `santiam_gpr`, `sf_mckenzie_cgr`,
`willamette_hcr_columbia` — plus `columbia_gcl_bon`, `snake_lwg_columbia` and
`sacramento_shasta_delta`.

**So the index lists walks, not rivers,** and `?river=willamette` matches the one walk
whose key begins with it (`willamette_hcr_columbia`) and answers for that walk alone — 1
of the basin's 9. No record on any Willamette walk carries `attrs._bank.river`, so each
of the nine is named by its own key; nothing in Neon says the nine are one river.
**Cure on the branch:** `/index` serves `walk_count`, says `entries_are: "one per banked
and published WALK, not one per river"`, and carries `entries_absence` — the structure is
the `geo.rivers.v1` registry's and is not banked in Neon (D-09-25-252 §5, pantry lane
d091790). Every `?river=` body carries `river_match`, which states what it matched, how,
and names the other banked walks it will not compose. The field keeps the name `rivers`
so that banking the structure is additive — and because `main.py` may only gain lines
(`test_R_no_existing_route_or_memo_changed`), a rename could not be carried into the
route's own docstring and would have made main.py the next thing that lied.

### C2 — §2.7's "it reproduces the pantry's order exactly" is wrong: the **item SET** is not the pantry's

§2.7 compared ORDERS and never checked the LISTS. `scripts/nwm_reach_gate.py` gates the
**crosswalk's placed set** (`places[k]`); this route's items are the records the **ledger
anchors to the walk**. On the Columbia those are not the same list:

| | items |
| --- | --- |
| this route, live (11 `cwms_project` records on the walk, by anchor km) | GCL 0.0, CHJ 82.0, **WEL 128.5**, **RRH 196.4**, **RIS 228.5**, **WAN 291.0**, **PRD 320.7**, MCN 489.9, JDA 614.0, TDA 653.7, BON 727.9 |
| the pantry's d091752 dry run (the 6 placed in `nwm_crosswalk_columbia`) | GCL, CHJ, MCN, JDA, TDA, BON |

The five in bold are the **mid-Columbia PUD projects** (`geo/cwms.MID_COLUMBIA`), which
the ledger anchors to this walk and the crosswalk does not place. What survives of §2.7:
the anchor km puts the **shared** items in exactly the pantry's printed order (GCL, CHJ,
MCN, JDA, TDA, BON), and the Snake's set and order match outright (LWG 0.0, LGS 59.6,
LMN 105.8, IHR 154.1 — the pantry's four, in its order). What does not survive is
"exactly": a verdict over 11 items is not a verdict over 6, however they are sorted.

**Cure on the branch:** `/gate` states its item set as an absence beside the verdict
(`items_absence`, the sentence D-09-25-252 §5 asks for), states that the verdict is this
API's own over this API's set and not the pantry's (`verdict_absence`), and names the
ported `gate_line` as **the function** ported byte for byte — not its item set. Three
route cells pin those sentences, and one pins the two sets against the d091752 fixture so
that the day the placed set is banked, the test says so.

### C3 — §7's slice note is right about WAN and BON, and wrong about what that costs

`gate_columbia.json` reads "no banked release" for **WAN** and **BON** because the values
bank holds `cwms_outflow_hourly` for GCL, PRD and MCN only. Live, one existence read per
location (the gate's own test, `LIMIT 1` per series) says **WAN and BON both publish a
release**, and **MCN alone does not** — so live, MCN is the only item that can never be
settled. The branch states that per item off the bank (`item_absence`) rather than naming
locations in code, so the slice's three (WAN, MCN, BON) and the live one (MCN) are both
true of their own bank.

MCN's `item_absence` says what it blocks: **the verdict over THIS route's item set**. It
makes no claim about the pantry's gate — and the review's own note that MCN "blocks this
API's gate even once the pantry's reads drawable" goes too far in the other direction:
`MCN` is an item of the pantry's placed set too (the d091752 dry run prints
`Q4 MCN: no banked release` on the Columbia), so the same item blocks that gate as well,
until MCN publishes a release or the crosswalk stops placing it.

### C4 — §6's counts are superseded, and the rehearsal is 8 breaks

`tests/test_hydro_rivers_d091770.py` is still 18 cells; the branch adds
`tests/test_hydro_rivers_routes_d091770.py` (**34 cells**: T ×5, M ×5, B ×8, A ×2, S ×14)
for a hydro total of **52**. The rehearsal is **8 of 8 caught by name** — R7 (the
statement timeout removed from `/index`) and R8 (the memo removed from `/index`) are
mutations of `main.py`, and each is caught only by a route cell, which is the measure of
what §6 was missing. The whole suite's counts on the branch and against main's own reds
are in the follow-up's own handback section below (§9).

### C5 — §8.4's "the five paths are asserted registered on `main.app`" was not asserted anywhere

It is now: `test_a_each_route_is_registered_on_main_app` reads `main.app.routes`, and
holds each of the five paths to GET, to its own endpoint function, and to nothing else
being mounted under `/api/hydro/rivers/`. A live HTTP smoke test is still not this lane's
(no deploy), but the routes are now exercised end to end through `TestClient` over the
banked cluster, which is what §6 should have said.

### C6 — §3.4's "`forecast` is served only when the line reads drawable" is now "never, and here is why"

`FORECAST_CYCLE_SQL` was **dead code**: `main.py` never executed it and `build_gate` was
never handed its rows (one grep over the tree, and the only other mention was the
tripwire's own list of statements). It is removed. A drawable line now earns
`forecast_absence: {absence: 'in_crosswalk_object'}`, because the series it would name are
`<feature_id>/<product>/<cycle>` and every `feature_id` is in the crosswalk object, which
this route does not read. A route cell drives the drawable branch with synthetic settled
receipts — the one path no bank reaches today — so the absence is asserted, not assumed.

### C7 — a live state that moved after the cut (not an error, a cut)

The Columbia crosswalk object **32** (banked 11:27Z with no publish row — §2.5's whole
case) was **published at 2026-10-17T16:05:44Z**. The fixture bank is the 11:30Z cut and is
sha-pinned, so `test_r1_index_crosswalk_published_and_pending` still asserts the pending
case and still should: it is the cut's truth, and the rule it proves ("serve the newest
PUBLISHED object, name the newer banked one") is what made the body right both before and
after that publish.

---

## 0. STOP-D: passed

`geo_display_objects` holds the published reach objects (15 rows) and
`geo_display_registry` holds a `geo.rivers.v1` registry
(`geo/rivers/registry.8f3d80e2.json`, 6,446 B, published 2026-10-17T11:05:11Z). Migration
322 is applied. The lane ran.

---

## 1. The short answer

Five routes are served, read-only, each from one transaction under
`SET LOCAL statement_timeout = '5s'` through a single-flight 300 s memo:

| Route | Today's answer on the live bank |
| --- | --- |
| `GET /api/hydro/rivers/index` | 15 banked+published walks; 3 carry a crosswalk, the rest read `no_crosswalk` **← wrong: 12 walks + 3 crosswalks; see Corrections (follow-up, 10-17) C1** |
| `GET /api/hydro/rivers/stations?river=` | the newest record per `source_key`, with the crosswalk **join** or the anchor |
| `GET /api/hydro/rivers/flow?river=&from=&to=` | per station, per series: `served` behind a settled receipt, else `{state:'grey', why}` |
| `GET /api/hydro/rivers/gate?river=` | **not drawable**, 0 banked `nwm_reach:*` receipts, recomputed by the ported `gate_line` |
| `GET /api/hydro/rivers/reaches?river=&from=&to=` | `{absence:'gate_not_drawable', line}` — no analysis read is taken at all |

**The one thing to read before anything else:** three fields the route spec asks for are
inside the *published bodies*, and this lane reads no R2. They are served as stated
absences with the pointer beside them, never derived. §2.1 is the whole of it.

---

## 2. What this spec got wrong (measured)

### 2.1 `length_km`, a station's placed `km`, and the river structure are NOT in Neon

The brief and the seed spec (`energylake-dashboard` §5) both ask the index for
`length_km`, and `stations` for `placed:{km, feature_id, dist_m}`. **Neither is reachable
without reading R2, and §5's own header forbids that** ("no R2 reads; the browser fetches
published objects"). The spec contradicts itself; measured 2026-10-17:

- `geo_layer_objects` **has no `length_km` column** (19 columns; the full list is in
  `ddl_geo_tables.sql`). The Columbia's 723.886 km is the crosswalk object's
  `reach.length_km`.
- A station's km / `feature_id` / `dist_m` live in the crosswalk object's
  `places[<source_key>]`.
- `geo_display_registry` and `geo_display_objects` hold **pointers only**: `registry_key`,
  `sha256`, `bytes`, `published_at`, `schema` / `object_id`, `display_key`, `sha256`,
  `bytes`, `content_type`, `published_at`. **`bytes` is a bigint BYTE COUNT, not the
  body.**

**What I served instead.** The route hands the page the pointer, the record's own
`record_sha256`, and the rule — and the page, which fetches and sha-checks those objects
already, does the join:

```
"placement": "join_crosswalk_places",
"placed": null,
"placed_absence": { "absence": "in_crosswalk_object",
  "join": { "object": "nwm_crosswalk_columbia", "url": "https://display.energylake.io/geo/nwm_crosswalk_columbia/417dfa03….json",
            "sha256": "417dfa03…", "path": "places['BON']",
            "key": "record_sha256", "record_sha256": "2ace3788…",
            "placed_iff": "the place exists AND its record_sha256 equals this record's AND dist_m <= 1000" } }
```

`length_km` is `null` beside `length_km_absence`; `label`, `basin` and which walks compose
one river are `null` beside `structure: {absence:'registry_not_read'}` with the registry
pointer. **A ruling is owed** (§8.1): either mirror `reach.length_km` and `places[]` into
Neon, or accept that these three are the page's to resolve.

### 2.2 "Bonneville at 723.886 km, the walk's figure" — it is neither

723.886 km is the **reach's length** (`reach.length_km`), and also BON's `feature_km`.
Bonneville's own place km is **723.746**. The brief conflated three numbers; the banked
crosswalk object says:

```
"reach": { "length_km": 723.886 }
"places": { "BON": { "km": 723.746, "feature_km": 723.886, "dist_m": 156.0,
                     "feature_id": 23736161, "record_sha256": "2ace3788…" } }
```

d091763 §2.4 was right and the brief's restatement of it was not. The record's anchor says
`[727.887, 728.371]` — a **different scale** (§2.3).

### 2.3 `km_from_reach_start` is an INTERVAL, not a value

The brief calls the anchor `{reach, reach_object_sha256, km_from_reach_start}`. It is an
interval `[lo, hi]` on NHD's own `lengthkm` scale — Bonneville `[727.887, 728.371]`,
Grand Coulee `[0.0, 0.087]`. The walk's measure puts Bonneville 4.5 km away, so the body
serves it with `km_is_an_interval: true`, `scale: 'nhd_lengthkm'` and the reason, and never
as a placement.

### 2.4 "geo_layer_objects (29 rows) … geo_records (65: cwms_project 33 …) … geo_unit_receipts (39 rows)"

**The bank moved under the lane while it ran.** At the cut (11:30Z) Neon held
`geo_layer_objects` **30**, `geo_records` **70** (cwms_project **38**),
`geo_unit_receipts` **44**, `geo_display_objects` 15, `geo_display_registry` 1. Two things
landed mid-session:

- **d091775, the mid-Columbia, at 11:22:17Z** — WEL, RRH, RIS, WAN, PRD on the banked
  Columbia walk, with four **settled** `cwms_outflow_hourly:…@mid_columbia` receipts and
  one inconclusive forebay receipt. These are now served (§3.3), and they are why the
  relay half of the ported scope rule is exercised rather than theoretical.
- **a new `nwm_crosswalk_columbia` object at 11:27:01Z** — object 32,
  `r2_sha256 caaf7c89…`, **with no publish row** (§2.5).

That is also why the suite runs on a cut and not on live Neon.

### 2.5 "the newest derived object" is the wrong rule — it must be the newest PUBLISHED one

This one the live bank forced, ten minutes before I wrote the read. Taking the newest
derived object per `layer_code`, as the pantry's own gate does, picks object **32** for the
Columbia's crosswalk — which has no `geo_display_objects` row, so the page has no bytes to
fetch, so the Columbia would have read **`no_crosswalk`** while a perfectly good published
crosswalk (object 9) sat beside it.

`WALKS_SQL` therefore takes the newest derived object **that has a publish row**, and names
the newer banked one beside it:

```
"crosswalk": { "object_id": 9, "r2_sha256": "417dfa03…", "sha_equal": true,
  "newer_banked_not_published": { "object_id": 32, "r2_sha256": "caaf7c89…",
    "fetched_at": "2026-10-17T11:27:01+00:00",
    "why": "a newer derived object of this layer is banked but has no geo_display_objects row; the published one above is served until it is published" } }
```

A walk with **nothing** published is omitted from `rivers` into `omitted[]` with its
reason, per D-09-25-247 §3 ("banked **and published**"). R6 in the rehearsal is this exact
mutation and it is caught.

### 2.6 The gate's own `why` cannot carry hour pairs, because they are not banked

`geo_unit_receipts` has **no `_detail` column**. The pantry's reach lines read
`… inconclusive (None, n 0, hour pairs 0.8674–1.4708)` because `compute()` has the receipt
in memory; a line recomputed from the bank can only read `… (None, n 0)` — which is exactly
the shape of the pantry's own `receipt …` stdout lines. The body states this as
`hourly_pairs_absence`. The port is held against the fixture (which *does* carry `_detail`)
byte for byte, and against the bank without it, so both halves are pinned.

### 2.7 The gate's item ORDER is the crosswalk's, and for a gauge river it is unreachable

**→ Corrected: the ORDER was never the whole of it — the item SET is not the pantry's. See Corrections (follow-up, 10-17) C2.**

`nwm_reach_gate.compute` orders items by `places[k]["km"]` — the crosswalk's measure. From
Neon I can order **dam** rivers by the ledger's own anchor km, and it reproduces the
pantry's order exactly (Columbia GCL→WAN→PRD→MCN→BON; Snake LWG→LGS→LMN→IHR). **A gauge
record banks no anchor at all**, so a gauge river (Sacramento, Q3) has no km in Neon; the
body orders by `source_key` and states `order_absence`. The order decides only *which item
is named as deciding*, never `drawable`, so today's answer is unaffected — but it would
matter the day a gate settles.

### 2.8 `attrs._bank.receipt_scope` exists, and the bank's own word beats my port

d091775's records carry an explicit `receipt_scope` map per dataset. The body uses it as
stated and falls back to the ported `geo/cwms.scope_subject`, reporting which:

```
PRD cwms_outflow_hourly -> "cwms_outflow_hourly:Flow-Out.Ave.1Hour.1Hour.CBT-REV@mid_columbia"
```

**The port reproduces that byte for byte** (test `test_u1_the_ported_scope_equals_the_banked_scope`),
and when the two disagree the body serves the bank's and states the disagreement rather than
resolving it.

### 2.9 The published objects may not be reachable yet

d091763 §1 measured `display.energylake.io` answering **404** for the `geo/` keys. This lane
reads no R2 and did not re-measure it, so every `url` in every body is **the key the ledger
published, not a URL I have seen answer**. Worth one curl before the page lane trusts them.

### 2.10 Smaller ones

- The kind is `cwms_project`; gauges carry `attrs._bank.reach` (the layer_code) and **no**
  `attrs._bank.river`, so the Sacramento walk has no river tag in the ledger at all and is
  named by its own key. Which walks compose "willamette" or "sacramento" is the registry's.
- `MCN` holds **no** `cwms_outflow_hourly` series — the existence read returns 4 rows for 5
  locations (§5.2). The brief's "MCN outflow grey 'not served'" is right, and the record's
  own `not_served` map is where it is said.
- `geo_crosswalk` (24 rows) is a **record↔NID dam** crosswalk (rules C3/C4/C4R), not the
  NWM reach crosswalk. No route reads it.

---

## 3. The routes, and what each is entitled to say

### 3.1 `/index`
From `geo_layer_objects` (newest **published** derived per `layer_code`),
`geo_display_objects`, `geo_display_registry` and the records' river tag. Serves per river:
the walk object with `r2_sha256`, the publish row, `sha_equal`, `feature_count`,
`vertex_count`, the source and its `terms_ref`; the crosswalk term or `no_crosswalk`;
the registry pointer; `omitted[]`. **`length_km`, `label`, `basin` are absences** (§2.1).

### 3.2 `/stations`
Newest record per `(kind, source_code, source_key)` through one `LATERAL … LIMIT 1` per key
— so the superseded CWMS copies (57–66) are never served, which matters because **BON's
`record_sha256` differs between the copies** (`f9750996…` superseded, `2ace3788…` newest)
and only the newest matches the crosswalk's place. With a crosswalk: the join above.
Without one: the record's published lat/lon and its anchor, `scale: 'nhd_lengthkm'`,
`placement: 'client_snaps_to_walk'`.

### 3.3 `/flow`
Per station, per dataset the record's own bank names. **Served only when the subject's
newest receipt set settles the tag the rows were served in**; else `{state:'grey', why}`.
Four distinct greys, each with its own sentence: `not served: <tag>` (the source serves
none), `no settled unit receipt for subject <s>` (the receipt gate), `no banked rows in the
window`, and a tag the set does not settle (D-08-02-R). Measured on the banked cut:

| station · dataset | subject | state |
| --- | --- | --- |
| GCL · outflow | `cwms_outflow_hourly` | **served**, cfs, receipts 31+33 |
| PRD · outflow | `…CBT-REV@mid_columbia` | **served**, cfs, receipts 137–140 |
| WAN · forebay | `cwms_forebay_hourly:…@mid_columbia` | grey — newest set settles no tag (141, inconclusive) |
| PRD · forebay | `…@mid_columbia` | grey — `not served: PRD.Elev-Forebay…` |
| MCN · outflow | `cwms_outflow_hourly` | grey — `not served: MCN.Flow-Out…` |

The NWM term is `drawn: false` with the gate's line, and the `feature_id` enumeration is an
absence (§2.1). **Velocity is never read** (§6). A window over 14 days is **refused** (400),
never clipped; each `LATERAL`'s `LIMIT` is the window's row count at the dataset's cadence.

### 3.4 `/gate`
Recomputed from banked `nwm_reach:*` receipts by `gate_line`/`line_of`, ported byte for
byte. Today: **0 receipts banked, not drawable, both twins.** [`forecast` is never served; see Corrections C6.] `forecast` is served only when
the line reads drawable. A river without a crosswalk reads `no_crosswalk`.

### 3.5 `/reaches`
`{absence:'gate_not_drawable', line}` — and it takes **no analysis read at all** while the
gate is shut (D-09-25-232 §3). When a gate does settle it still needs the crosswalk's
`feature_id`s, which is stated as `in_crosswalk_object`.

---

## 4. The ported constants, and their source shas

All from `energylake-pantry` @ **`f2a75d16dcc0761f358b4ee8a33790e3d02256ac`**:

| ported | from | sha256 of the source file |
| --- | --- | --- |
| `SERIES`, `IRREGULAR`, `ALL_SERIES`, `DEFAULT_VERSION`, `VERSIONS`, `WILLAMETTE`, `MID_COLUMBIA`, `BASINS`, `RELAY_BASINS`, `default_version`, `version`, `ts_name`, `receipt_subject`, `relay_basin`, `scope_subject` | `geo/cwms.py` | `bf49261d9485be23def63e87ed2dc5226c14da49f4eccf96d5ab5bac2de76d19` |
| `receipted_tags` | `ingesters/usgs_iv.py` | `4fab3b708ff7f5583bea802307ed9cc461b6af49aca178078827190c9e09b9d1` |
| `line_of`, `gate_line`, `flow_verdict`, `LAYER_REACH`, `TOL`, `MIN_DAYS`, `FT3` | `scripts/nwm_reach_gate.py` | `be3d5365f2d7a03c93fd5c005ea7ec1102964c2525e786ac71dc21e0991a7825` |
| `MIN_DAYS = 20`, `OUTFLOW_TOL = 0.20`, `FT3_PER_M3 = 35.3147` (values) | `geo/units.py` | `22e44fd91c1a1ba38e8905df320343340b68af1ee47c6456fdd2d885ff049453` |
| the five geo tables' DDL, verbatim (302 lines 92-199, 322 lines 114-148) | `migrations/302…`, `migrations/322…` | `a149a16b…`, `dbc0287b…` |

The gate port is pinned against the pantry's **dry-run stdout**, banked by the dashboard
(`energylake-dashboard` @ `3b2ba7f`): `gate_receipt.json` `d74ce5a3…`, `gate_stdout.txt`
`b2d828f2…` — the shas d091763 §4 states.

---

## 5. The EXPLAIN receipts

Full plans: `docs/receipts/hydro-rivers-api-d091770/explain_neon.txt`. Taken on **Neon's own
planner over the live tables**, because the test cluster's `timeseries_values` holds 6
banked rows and a Seq Scan is genuinely cheapest there — a plan taken on the fixture would
prove nothing about D-08-02-L. The suite's X1 cell forces `enable_seqscan = off` and asserts
the plan's **shape**.

| read | index | plan | time | buffers |
| --- | --- | --- | --- | --- |
| `/flow`, `/reaches` window (4 pairs × 7 d, LIMIT 170) | `idx_tsv_series_ts` | **Index Scan**, backward, 4 loops | 20.5 ms | hit 47 read 106 |
| `/gate`, `/flow` arbiter existence (5 series, LIMIT 1) | `idx_tsv_series_ts` | **Index Only Scan**, 5 loops | 1.87 ms | hit 30 read 5 |
| newest record per `source_key` (60 keys) | `ix_gr_kind_key` | Index Scan + LIMIT 1 per key | 1.12 ms | hit 129 read 13 |
| newest published object per `layer_code` (15) | `ix_glo_layer` | two Index Scans + LIMIT 1 | 1.63 ms | hit 91 read 7 |

**No Seq Scan on `timeseries_values` in any plan.** The window and both key columns are all
in the `Index Cond` — nothing is filtered after the fact. The only Seq Scans anywhere are
the key-list CTEs over `geo_records` (70 rows, 12 buffers) and `geo_layer_objects` (30
rows, 4 buffers), which is what enumerating keys costs.

**The existence read doubles as a measurement:** five locations in, **four rows out** — GCL,
CHJ, PRD and BON answer and **MCN does not**. That missing row *is* the gate's "no banked
release" and `/flow`'s grey on MCN, measured rather than assumed.

---

## 6. Tests, sweeps, rehearsal

**`tests/test_hydro_rivers_d091770.py`: 18 cells.** (The routes themselves are untested here — see Corrections C4 and C5.)

- **Red on main `b399c67`:** a clean worktree with only the test, loader and fixtures fails
  at collection — `ModuleNotFoundError: No module named 'hydro_rivers'`
  (`red_on_main.txt`).
- **Green on the branch:** 18/18.
- The bank is a **sha-checked slice** of each table, cut 2026-10-17T11:30Z: each file is the
  exact `string_agg(row_to_json(t)::text, …)` text Neon returned, accepted only after the
  local sha256 equalled the one **Neon** computed over the same expression. The geo tables
  are the pantry's migrations verbatim; `timeseries_values`/`datasets`/`sources` come from
  Neon's live definition (they predate the numbered migrations) and were verified column for
  column.
- Cells: R1 ×2 (index: published-vs-pending, the Willamette's absence, the stated
  absences) · R2 ×3 (newest record, the join, the anchor) · U1 ×2 (the newest receipt set;
  the port equals the banked scope) · R3 ×2 (the four greys; the window refusal) · R4 ×4
  (the stdout port, the verdict floor, not-drawable, `no_crosswalk`) · R5 ×2 (tripwires) ·
  X1 ×2 (plans).

**R — the mutation rehearsal** (`tests/rehearse_hydro_rivers_d091770.py`, `rehearsal_R.txt`):
**6 of 6 breaks caught by name**, tree hash `95c3292059dee413…` before and after.

| break | caught by |
| --- | --- |
| R1 `DISTINCT ON` for the newest record | `test_r2_stations_newest_record_per_source_key`, `test_r5_no_banned_sql_in_any_statement`, `test_x1_the_ledger_reads_take_one_row_per_key` |
| R2 OR-ed series in the window read | `test_r5_every_series_read_is_one_dataset_series_pair`, `test_r5_no_banned_sql_in_any_statement` |
| R3 a series served without its receipt | `test_r3_flow_serves_only_behind_a_settled_receipt` |
| R4 the gate drawable at n < 20 | `test_r4_flow_verdict_needs_twenty_days_and_the_tolerance` |
| R5 NWM velocity read into a body | `test_r3_flow_serves_only_behind_a_settled_receipt`, `test_r5_no_route_reads_velocity` |
| R6 newest derived instead of newest **published** | `test_r1_index_crosswalk_published_and_pending` |

**The rehearsal found two holes in my own suite and one test side-effect**, all fixed before
this handback:

1. **R3 was not caught.** Every station in the first slice was either served or
   source-refused, so the receipt gate itself was never the governing branch — removing it
   left PRD's forebay grey for a *different* reason and the suite stayed green. Fixed by
   banking **WAN**, the one station in the bank that *serves* a forebay whose relay subject
   is banked inconclusive, and by asserting the **why**, not just the greyness.
2. **R4 was not caught.** A banked receipt carries its own verdict and the fixture carries
   the pantry's, so nothing in the suite ever read `MIN_DAYS`. Fixed with a cell over
   `flow_verdict` directly.
3. **X1 wrote a plan file into the tree**, which broke the rehearsal's "left no trace"
   claim. The test writes nothing now; the plans live in `docs/receipts/`.

**Source tripwires (R5).** Over every statement the lane can emit — the five SQL constants,
both `series_window_sql` orders, and `main.py`'s one inline read: no `DISTINCT ON`, no cast
in `ORDER BY`, no OR-ed series, and no mention of velocity. Over the sources: no velocity
dataset, series, column or route, and **no line that mentions velocity also reads anything**.
The bodies *do* carry a `velocity` key — it holds the absence that says the term is never
served.

**The whole suite**, `python3 -m pytest tests/ -q`:

| tree | passed | failed | skipped |
| --- | --- | --- | --- |
| main `b399c67` | 3,492 | **4** | 2 |
| this branch | **3,512** | **4** | 0 |

**The four failures are identical on both trees and are main's own**: `asset_runs` H8 ×2
(the brief names these), `test_chart_brief_maps_contract`, and `tropics` T1. The +20 is this
lane's 18 cells **plus 2** `test_mjo_status` cells that skip on main for "no
energylake-pantry checkout beside this repo" and run here because this session cloned the
pantry beside the repo — an environment effect, not a code one.

**Two existing tripwires fired during the lane and both were obeyed rather than loosened:**

- `test_season_regions::test_p1_source_sweep` forbids `_pool.connection(` anywhere from
  `import season as _season` to end of file. The rivers section was appended after it, so a
  rule that was never about this code tripped on position. **Fixed by moving the rivers
  section above the season import**, keeping season's block season's alone.
- `tropics::test_R_no_existing_route_or_memo_changed` requires `main.py` to **only gain
  lines**. The move dropped one trailing blank line; restored, so the diff is purely
  additive (`git diff b399c67 -- main.py` has zero removed lines).

---

## 7. The banked bodies and the receipts

`docs/receipts/hydro-rivers-api-d091770/bodies/` — **the page lane's vectors**, built by the
routes' own builders over the sha-checked bank, `SHA256SUMS.json` beside them.
Rebuild: `cd tests && python3 -I ../docs/receipts/hydro-rivers-api-d091770/bank_bodies.py`.

| body | bytes | sha256 |
| --- | --- | --- |
| `index.json` | 22,586 | `ad0e9d477078132b…` |
| `stations_columbia.json` | 18,584 | `395ab03d2418cd15…` |
| `stations_willamette.json` | 4,713 | `deb2ab887d0ce669…` |
| `flow_columbia.json` | 20,279 | `5e542b19551606ee…` |
| `flow_willamette.json` | 4,922 | `de2db35a8969edc0…` |
| `gate_columbia.json` | 6,302 | `d6d9a82620e52ca3…` |
| `gate_willamette.json` | 462 | `2f5803945cd2098a…` |
| `reaches_columbia.json` | 1,167 | `1b216750d43a3f91…` |
| `reaches_willamette.json` | 581 | `8ac6cfa57b16935b…` |

**→ Corrected in part: see Corrections (follow-up, 10-17) C3.**

**These are vectors over the banked CUT, not over live Neon.** The values bank holds
`cwms_outflow_hourly` for GCL, PRD and MCN only, so in `gate_columbia.json` WAN and BON read
"no banked release" — true of the slice, **not** of the live bank, where both publish a
release. The live gate line is the one in §3.4: not drawable, decided by Q4 GCL, 0 receipts.

| file | what |
| --- | --- |
| `explain_neon.txt` | the four plans, §5 |
| `red_on_main.txt` | the suite failing on `b399c67` |
| `rehearsal_R.txt` | 6 of 6, tree unchanged |
| `suite_branch.txt` | the whole suite's tail |
| `bank_bodies.py` | rebuilds `bodies/` |
| `tests/fixtures/hydro_rivers_d091770/manifest.json` | every bank's slice, rows, chars and Neon's sha |
| `tests/fixtures/hydro_rivers_d091770/bank_from_neon.py` | the read's shape; see §8.3 |

---

## 8. Not done, or not mine

### 8.1 A ruling is owed: `length_km`, `places[]` and the river structure

The route spec cannot be satisfied as written (§2.1). Three ways out, none of them this
lane's to choose:

1. **Mirror them into Neon** — a `length_km` column on `geo_layer_objects`, and the
   crosswalk's `places[]` as rows. Then the routes serve numbers and the absences go. Pantry
   lane.
2. **Accept the join as the page's** — what this lane serves. The page already fetches and
   sha-checks both objects, so it costs it nothing, and D-09-25-237's "keyed by
   `record_sha256`" is honoured exactly.
3. **Let the API read R2** — reverses this lane's charter and §5's own header.

I recommend 2 for now and 1 when a second consumer appears, and the bodies are shaped so
that 1 is additive: `length_km` and `placed` are already the keys, holding `null`.

### 8.2 The gate's gauge order (§2.7) and the forecast's `feature_id`s

Both need the crosswalk's content. Harmless today — the gate is shut — and both are stated
absences rather than guesses.

### 8.3 `bank_from_neon.py` is banked but was never run here

It holds the read's shape, including the `default_transaction_read_only = on` read-back the
brief asks for. **It could not run in this environment**: the network policy allows no
Postgres connection, only HTTPS through the agent proxy (`psycopg` cannot resolve the Neon
host), so the bank was taken through the MCP as d091763 took its own — the exact
`string_agg` text, accepted only against Neon's own sha256. The read-only *property* is
therefore asserted by the MCP's SELECT-only usage and the absence of any write, not by a
flag read back in-session. If that receipt is wanted literally, the environment needs
Postgres egress (or the script run from a box that has it).

### 8.4 Not measured

- **`display.energylake.io` reachability** (§2.9) — one curl, not this lane's.
- **The Sacramento gauges and the eight other Willamette walks** — outside the asserted
  slice. `/index` over the live bank is reported in §1 and §5 from its own read.
- **`nwm_reach_analysis_hourly` / `nwm_reach_forecast_hourly`** — no plan and no bank,
  because no route reads them while the gate is shut.
- **A live HTTP smoke test** of the five routes — no deploy in this lane; the bodies are
  built by the routes' own builders over the bank, and the five paths are asserted
  registered on `main.app`. **← the last clause was not true: nothing asserted it. See
  Corrections (follow-up, 10-17) C5.**

---

## 9. The follow-up (10-17): what it added, and its gate

**Branch:** `claude/rivers-followup-d091770`, cut from main `4016beb` (this lane merged as
PR #120). **No PR, no merge, no deploy. Writes: none** — Neon was read through the MCP
with `SELECT` only (`followup_neon_measurements.md`), and the suite runs on the sha-pinned
11:30Z slice, never on live Neon.

### 9.1 What changed

| | |
| --- | --- |
| `tests/test_hydro_rivers_routes_d091770.py` | **new, 34 cells.** The five routes through FastAPI's `TestClient` over the lane's throwaway Postgres, with `main._pool` swapped for `load_bank_d091673.PgPool`, which records every `(sql, params)`: **T** ×5 the statement timeout is the FIRST statement on the connection · **M** ×5 a memo hit makes NO reads and serves the same body · **B** ×8 the 400s (unknown river, no river, the ambiguous resolver, a window over 14 days refused and not clipped, and which route takes which parameter) · **A** ×2 each path registered on `main.app`, GET, its own endpoint, nothing else under the prefix · **S** ×14 the stated absences in the bodies the routes really serve |
| `hydro_rivers.py` | `/gate` carries `items_absence` (the D-09-25-252 §5 sentence), `verdict_absence`, `item_count`, `items_are`, a `line` and an `item_absence` per item; `/index` carries `walk_count`, `entries_are`, `entries_absence`; every `?river=` body carries `river_match`. `FORECAST_CYCLE_SQL` and the dead `cycle_rows` parameter are gone; a drawable line earns `forecast_absence` |
| `main.py` | **+23 lines, 0 removed** — the five route docstrings say what the bodies now state. `test_R_no_existing_route_or_memo_changed` (main.py may only gain lines) stays green |
| `tests/rehearse_hydro_rivers_d091770.py` | **R7** the statement timeout removed from `/index`, **R8** the memo removed from `/index` — both mutations of `main.py`, both caught only by a route cell. The rehearsal now runs both hydro test files |
| `docs/receipts/.../bodies/` | re-banked by `bank_bodies.py` over the same sha-checked slice, `SHA256SUMS.json` beside them |

### 9.2 The gate

**The whole suite** (`python3 -m pytest tests/ -q`), same box, same interpreter, main read
from a worktree of `origin/main`:

| tree | passed | failed | skipped |
| --- | --- | --- | --- |
| main `4016beb` | 3,539 | **3** | 2 |
| this branch | **3,573** | **3** | 2 |

**+34 passed, exactly the new route cells. The 3 failures are the same 3 test ids on both
trees and are main's own:** `test_H8_a_rebanked_vector_is_mains_plus_the_one_key` ×2
(`asset_wind_57514.json`, `body_wind_ciso_n28.json`) and `tropics::test_T1_scopes_recent
_and_all_and_a_bad_scope` (date-aged). The 2 skips are `test_mjo_status`'s, for "no
energylake-pantry checkout beside this repo" — there is none in this session, which is
also why §6's "+2 mjo cells" does not repeat here. **`season_regions` p4 and the two
`vintages` timezone cells pass on this box**, on main and on the branch alike.

**The mutation rehearsal:** `8 of 8 breaks caught by name`, tree hash unchanged
(`followup_rehearsal_R.txt`).

**`git diff --diff-filter=D --name-only origin/main...HEAD`: empty** — no file is deleted.

### 9.3 Receipts

| file | what |
| --- | --- |
| `followup_suite_branch.txt` | the whole suite on the branch |
| `followup_suite_main.txt` | the whole suite on main `4016beb`, same box |
| `followup_rehearsal_R.txt` | 8 of 8, tree unchanged |
| `followup_neon_measurements.md` | the five read-only statements behind C1–C3 and C7 |

### 9.4 Still not done, and now named

- **The placed set is still not banked** (D-09-25-252 §5, pantry lane d091790). Until it
  is, `/gate` gates this API's item set and says so; the cell that pins the two sets
  against the d091752 fixture is what will go red when it lands.
- **The river structure is still not banked** (same ruling). `/index` keeps the field name
  `rivers` and states that its entries are walks; `?river=` answers for one walk.
- **A live HTTP smoke test** — still no deploy in this lane. The routes are now exercised
  end to end against the banked cluster, which is as far as a read-only branch goes.
