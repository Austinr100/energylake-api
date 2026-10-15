# Handback d091741: the API serves the Tropics index (ground, products, text, members) and reads no R2

**Lane:** d091741 · **Repo:** energylake-api · **Branch:** `claude/tropics-index-api-routes-lnf4xn`, from main at `265f9fb`.
**Ran:** 2026-10-15, 13:46Z–14:35Z (container clock). **Environment:** EL 6.
**Rulings held:** D-09-25-226 · D-09-05-T · D-09-21-05 · D-09-09-M · D-09-25-75 (CLAUDE.md).

**Scope kept:**
- Branch only. No PR, no merge, no deploy.
- **No Neon write.** Every Neon statement was a SELECT, or an EXPLAIN (ANALYZE, BUFFERS) of one, through the Neon connector.
- **No R2 read.** The routes read none; this lane fetched none.
- **Network:**
  - GitHub: energylake-pantry, read only, at `81ad901`.
  - `gibs.earthdata.nasa.gov`: tiles, layer metadata, capabilities.
  - `display.energylake.io`: 4 GETs, all 404 (§1).
  - **One call outside the brief's stated GitHub purpose:** `github.com/nasa-gibs/gibs-api-docs`, for GIBS's published acknowledgement (§4). GitHub is on the host list, but the brief scoped it to the pantry. I judged the brief's own "read GIBS's published attribution" to cover it; say if it did not.

**Read first:**
- this repo's `CLAUDE.md`, `tropics.py`, and the tropics routes in `main.py`;
- the d091673 and d091682 tests and handbacks;
- pantry recon `docs/recon_2026_10_15_tropics_v2_layers_d091737.md` (all of it; §1 and §7 closely), its fixtures and receipts;
- pantry migrations 291, 294 and 297; `tropical/keys.py`, `nhc.py` and `capture.py`.

**d091740:** not landed, and its spec was not readable from here (§1.1). Built against recon §7's keys and the recon's parsed fixtures, as the brief allows. That contract is stated in §3 and in `tropics_index.py`'s docstring.

---

## 0. In ten lines

1. **Four new routes** in `/tracks`' house shape (storm as a query parameter, `absence {reason, detail}` per block, `not_served` stated, 404 for an unknown storm): `/ground`, `/products`, `/text`, `/members`. The SQL, shaping and probe are in `tropics_index.py`; the routes are new lines in `main.py`'s tropics section.
2. **`/ground` probes GIBS tile by tile and never binds `default`.** A slot is present only on a 200 PNG, and when GIBS sends `layer-time-actual` it must equal the slot. A live finding here: GIBS states the time it served.
   - Live runs: East and West frames 29.4–44.7 minutes old.
   - Frames land out of order: East 13:20 was a hole at 13:54Z and 13:59Z and present by 14:20Z.
3. **The GIBS acknowledgement sentence is read and pinned** (§4): "We acknowledge the use of imagery provided by services from NASA's Global Imagery Browse Services (GIBS), part of NASA's Earth Science Data and Information System (ESDIS)." The recon's from-memory version was close but not exact.
4. **`/products`, `/text` and `/members` are an index of ledger rows** (`tropical_file_vintage_in_force`). Nothing is fetched: the URLs are `display.energylake.io/<r2_key>`.
5. **Today every banked product answers `not_published`.** Neon holds no `display:*` row; d091740 has published nothing.
6. **`not_issued` is said only on NHC's own word:** the newest CurrentStorms entry the bank holds for the advisory (`storm_entry.meta.refs`).
   - Rachel 058 and Simon 032 link no `ww_kmz`: `not_issued`.
   - Isaias 013A's entry links 013's surge KML: `not_issued`, naming 013.
7. **The default advisory is NHC's newest, by its entries, not the newest official forecast.** Isaias's last five advisories (016–020) are post-tropical and text-only, so "newest official" would serve 015 on `/text` while NHC's last word is 020. This is a correction to the brief (§8).
8. **Every ledger read is an index read on Neon** (`tfv_identity`), each under 0.4 ms; the one track-points read is 2.7 ms. No index was added (§5).
9. **Tests:**
   - **Red on main:** 81 of 87 red; the 6 that pass check fixtures only.
   - **Green on the branch:** 87 of 87.
   - **Rehearsal:** 20 of 20 breaks red, the brief's three among them; tree restored byte for byte.
   - **Existing tropics suites:** 115 of 115 green.
   - **Whole suite:** see §6.4.
10. **A real bug was found live and fixed:** one httpx client kept across event loops ("Event loop is closed" on the second probe). It now has its own test and rehearsal break (§4.3).

---

## 1. Measured before building

### 1.1 What exists

| what | measured | where |
|---|---|---|
| d091740's spec `claude/cc_spec_2026_10_15_tropics_publish_products_d091740.md` | **not found**: no such path or branch in energylake-pantry (335 refs listed), nothing in energylake-architect (main `8e0a2ba`, no `claude/` dir) | `git ls-remote`, clone |
| `display:*` rows in the ledger | **0** of 7,029 | Neon, 13:56Z |
| display bucket `tropics/…` | `tropics/ep202026/adv032/cone.geojson`, `tropics/wsp/2026101506/wsp34_bands.geojson` and `tropics/ep202026/2026101112/ecmwf_ens/manifest.json` all **404** | GET, between 13:50Z and 14:00Z |
| `tropical_file_vintage.source` CHECK | `nhc, nhc_ftp, ecmwf_aws, hafs_aws, gfs_aws, parsed`: **no `display`** | `pg_constraint` |
| the ledger's indexes | `tfv_identity (source, product, storm_id, vintage_key)`, `uniq_tropical_file_vintage (… status, sha256)`, `tfv_heartbeat`, `tfv_standing_absence`, the PK: **no `dataset` column in any** | `pg_indexes` |
| database collation | `C.UTF-8`, so a prefix `LIKE` is an index range | `pg_database` |
| the in-force file view | 294's `DISTINCT ON (source, product, storm_id, vintage_key)`. A WHERE on those four columns pushes through it (every plan in §5). | `pg_get_viewdef` |

### 1.2 What NHC said it issued: `storm_entry.meta.refs`

The bank keeps every revision of each CurrentStorms entry (`vintage_key '<ADV>:<sha8>'`). `meta.refs` names, per product, **the advisory whose file NHC linked**. That is NHC's own statement of what it issued with an advisory, and it is what `not_issued` reads:

| advisory | entries held | newest entry's `ww_kmz` | earlier entries |
|---|---|---|---|
| Rachel 058 | 4 | absent (fetched 21:31Z) | 15:02Z and 15:32Z linked **057A's** ww |
| Simon 032 | 2 | absent | absent |
| Isaias 015 | 5 | absent (14:59Z) | 08:58–09:58Z linked 014A's |
| Isaias 013A | — | `surge_ww_kml → 013` | — |
| Rachel 057A | — | `tcd → 057` (NHC issues no TCD with an intermediate) | — |

### 1.3 Isaias is text-only after 015

- Official forecast points stop at **015** (10-10 06Z).
- Entries, TCD and TCP continue through **020** (10-11 15Z). From 016 the entries link only `tcd`, `tcm` and `tcp`.
- The storm book's `inactive_reason` agrees: "last advisory TCP 020".

### 1.4 No live `not_banked`

The bank holds every text and geometry file any newest entry links. So T1's `not_banked` is constructed by removing Isaias 019's banked TCD in the second test layer only (§6.1).

---

## 2. The routes, with example bodies

Full bodies are in `docs/receipts/tropics-api-index-d091741/bodies/`, served from the test fixtures by `write_example_bodies.py`. Common to all four: 300 s single-flight memo, never stale, and the `cache` block and headers.

| route | params | answers | reads (one transaction, `SET LOCAL statement_timeout = '5s'` first) |
|---|---|---|---|
| `GET /api/weather/tropics/ground` | `basin=al\|ep\|cp` (required) | `{basin, layers[], attribution, absence, probe, frame_rule, recent_rule, basin_rule, timestamps, not_served, cache}` | **no table**. GIBS z0 tiles, 4 s per tile, a 20 s search budget. Memo per layer (ep and cp share West's). |
| `GET /api/weather/tropics/products` | `storm_id`, `advisory` (NNN or NNNA; default NHC's newest) | `{storm_id, name, basin, advisory, advisory_requested, init_ts, position_valid_ts, issued_ts, nhc_entry, nhc_entry_absence, revision, corrected, objects[], absent[], wsp{cycle, synoptic_ts, objects[], absent[], absence}, absence, …rules, reasons, not_served}` | STORM_SQL; ENTRY_NEWEST_SQL or ENTRY_SQL; ADVISORY_AT_SQL; FILES_SQL (the advisory); FILES_SQL (the WSP cycle) |
| `GET /api/weather/tropics/text` | `storm_id`, `advisory` | `{…the same head…, products{tcd, tcp}, absence, times_rule, sections_rule, …}` | as /products, no WSP read |
| `GET /api/weather/tropics/members` | `storm_id`, `cycle` (YYYYMMDDHH; default every cycle held) | `{storm_id, name, cycle, cycles_held, cycles[{cycle, ensembles[]}], absent[{cycle, ensemble, reason, detail}], absence, ensembles, members_rule, reasons, not_served}` | STORM_SQL; MEMBERS_SQL |

**Status codes:**

| status | when |
|---|---|
| 404 | unknown storm; or, on `/products` and `/text`, a named advisory the bank knows nothing of (no official forecast, no CurrentStorms entry, no file) |
| 400 | bad parameters. Nothing is normalised: `advisory=32`, `032a`, `cycle=2026101113` and `cycle=2026023112` are all refused. |
| 503 | DB unavailable or a read past 5 s (the house's `_solar_serve`). On `/ground`, the 30 s build backstop. |
| 200 with `absence` | everything else, including a known storm with nothing published |

**Absence reasons:**

| route | reasons |
|---|---|
| `/products` | `not_issued` · `basin_has_none` (surge: `al` only) · `not_banked` · `not_published`. WSP: `not_banked`, `not_published`, `no_synoptic_time`. |
| `/text` | the same, minus `basin_has_none` |
| `/members` | `not_banked` · `not_published` · `too_few_members` (the manifest's own `n_members < min_members`, never an API count) |
| `/ground` | `no_frame_in_window` · `gibs_unreachable` |

### 2.1 `/ground?basin=al`

Replayed from this lane's 14:20Z z0 recording (`bodies/ground_al.json`):

```json
{"basin": "al",
 "layers": [{"id": "GOES-East_ABI_GeoColor", "satellite": "GOES-East", "instrument": "ABI",
   "title": "GeoColor (True Color (Day), Multispectral IR (Night))",
   "template": "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-East_ABI_GeoColor/default/2026-10-15T13:40:00Z/GoogleMapsCompatible_Level7/{z}/{y}/{x}.png",
   "time_template": "…/GOES-East_ABI_GeoColor/default/{Time}/GoogleMapsCompatible_Level7/{z}/{y}/{x}.png",
   "tile_matrix_set": "GoogleMapsCompatible_Level7", "tile_size": 256, "minzoom": 0, "maxzoom": 6,
   "frame_utc": "2026-10-15T13:40:00+00:00", "probed_utc": "2026-10-15T14:20:00+00:00", "age_min": 40.0,
   "recent_present": ["2026-10-15T13:40:00Z", "…13:30", "…", "2026-10-15T11:50:00Z"], "recent_unknown": []}],
 "attribution": {"text": "We acknowledge the use of imagery provided by services from NASA's Global Imagery Browse Services (GIBS), part of NASA's Earth Science Data and Information System (ESDIS).", "source": "…", "layer_credit": "…"},
 "absence": null,
 "probe": {"probe": "gibs_geocolor_z0", "layer": "GOES-East_ABI_GeoColor", "probed_utc": "…14:20:00+00:00",
   "frame_utc": "…13:40:00+00:00", "age_min": 40.0, "slots_asked": 5,
   "absent_before_frame": ["…14:20", "…14:10", "…14:00", "…13:50"], "recent_present": 12, "refusal": null}, …}
```

The `"default"` in the URL is WMTS's **style** segment. The **time** segment is always the probed frame, never `default`; G1 asserts this for every URL asked and for the template.

**How the probe works:**
- **Frame:** descend from now in 10-minute slots; the first present slot is the frame; give up after 18 slots.
- **`recent_present`:** then ask the frame's 11 older slots together. A probe is therefore at most 18 + 11 tile GETs, typically 4–5 + 11 (~2.5 s through this session's proxy).
- **Refusals:** `layers: []` with `absence`:
  - `no_frame_in_window` ("18 ten-minute slots probed (… to …), none present");
  - `gibs_unreachable` (a transport error, an answer other than 200 or 404, or the 20 s budget).

### 2.2 `/products?storm_id=ep202026`

Default: NHC's newest, 032 (`bodies/products_ep202026.json`):

```json
{"storm_id": "ep202026", "name": "Simon", "basin": "ep", "advisory": "032", "advisory_requested": false,
 "init_ts": "2026-10-15T06:00:00+00:00", "position_valid_ts": "2026-10-15T09:00:00+00:00",
 "issued_ts": "2026-10-15T09:00:00+00:00",
 "nhc_entry": {"vintage_key": "032:63ae9aa6", "fetch_ts": "2026-10-15T09:49:33.813818+00:00", "classification": "TS", "refs": {"cone_kmz": "032", "tcd": "032", "tcp": "032", "…": "…"}},
 "revision": 0, "corrected": false,
 "objects": [{"product": "cone", "url": "https://display.energylake.io/tropics/ep202026/adv032/cone.geojson",
   "sha256": "23fb9e66…", "bytes": 27968, "source_r2_key": "tropical/nhc/2026/ep202026/adv032/cone_kmz.kmz",
   "source_sha256": "1f82e17d…", "geometry_types": {"Polygon": 1}, "vertices": 1260,
   "revision": 0, "corrected": false, "original": null, "source_in_force": true}],
 "absent": [{"product": "ww", "reason": "not_issued", "detail": "NHC's CurrentStorms entry for advisory 032 (the newest the bank holds, 032:63ae9aa6, fetched 2026-10-15T09:49:33.813818+00:00) links no ww_kmz"},
            {"product": "surge_ww", "reason": "basin_has_none", "detail": "NHC issues storm surge watches and warnings for the US Atlantic and Gulf coasts only; this storm's basin is 'ep'"}],
 "wsp": {"cycle": "2026101506", "synoptic_ts": "2026-10-15T06:00:00+00:00",
   "objects": [{"product": "wsp34_bands", "vertices": 29916, "…": "…"}, {"product": "wsp50_bands", "…": "…"}, {"product": "wsp64_bands", "…": "…"}],
   "absent": [{"product": "wsp34_grid", "reason": "not_published", "detail": "NHC's wsp_halfdeg_zip for cycle 2026101506 is banked (tropical/nhc/wsp/2026101506/wsp_halfdeg_zip.zip, sha256 8ee8d4c1…); the ledger holds no published display:wsp34_grid object"}, "…"],
   "absence": null}, "absence": null, "…": "…"}
```

The values above are from the test fixture: constructed display rows over the real ledger (§3). **Against Neon today**, the cone and the WSP bands would also be `not_published`.

**Other bodies in `bodies/`:**

| file | case |
|---|---|
| `products_ep182026.json` | Rachel 058: cone; ww `not_issued`; surge `basin_has_none`; WSP 2026101112 with all six objects |
| `products_al092026_014A.json` | Isaias 014A: ww and surge served; cone `not_published` |
| `products_al092026.json` | Isaias default = 020: everything `not_issued`; WSP `no_synoptic_time` |

### 2.3 `/products?storm_id=ep202026&advisory=032`: corrected (I2)

The second test layer (`bodies/products_ep202026_032_corrected.json`):

```json
"objects": [{"product": "cone", "url": "https://display.energylake.io/tropics/ep202026/adv032/cone.r1.geojson",
  "sha256": "b5743029…", "bytes": 27980, "vertices": 1264, "revision": 1, "corrected": true,
  "original": {"url": "https://display.energylake.io/tropics/ep202026/adv032/cone.geojson",
               "sha256": "23fb9e66…", "bytes": 27968, "absence": null},
  "source_in_force": true}],
"revision": 1, "corrected": true
```

- **The copy in force** is the view's: the newest non-anomaly copy, with `revision` and `corrected` from `meta.correction`.
- **The original** is the uncorrected banked copy (no `meta.correction`, the first fetched). It is read by one more `tfv_identity` probe, executed only where `corrected` holds (Neon: "never executed" today).

### 2.4 `/text?storm_id=ep202026`

From `bodies/text_ep202026.json`:

```json
"products": {"tcd": {"product": "tcd", "url": "https://display.energylake.io/tropics/ep202026/adv032/tcd.json",
  "sha256": "68ef33dc…", "bytes": 4776, "issued_utc": "2026-10-15T09:00:00+00:00",
  "transmitted_utc": "2026-10-15T08:39:00+00:00", "forecaster": "Reinhart",
  "sections": [{"title": "Discussion", "kind": "discussion"}, {"title": "Key Messages", "kind": "key_messages"},
               {"title": "FORECAST POSITIONS AND MAX WINDS", "kind": "forecast_table"}],
  "source_r2_key": "tropical/nhc/2026/ep202026/adv032/tcd.txt", "source_sha256": "5e97fb1d…",
  "revision": 0, "corrected": false, "original": null, "source_in_force": true, "absence": null},
 "tcp": {"…": "summary · watches_and_warnings · discussion_and_outlook · hazards_affecting_land · next_advisory"}}
```

`bodies/text_ep182026_057A.json`:
- tcd: `not_issued`, "links advisory 057's tcd: NHC issued none with 057A";
- tcp: `not_published`.

### 2.5 `/members?storm_id=ep212026`: Trudy, today

From `bodies/members_ep212026.json`: 9 cycles held, all from her A-deck.

```json
{"storm_id": "ep212026", "name": "Trudy", "cycle": null,
 "cycles_held": ["2026101306", "…", "2026101506"], "cycles": [],
 "absent": [{"cycle": "2026101306", "ensemble": "ecmwf_ens", "reason": "not_banked", "detail": "the bank holds no tracks:ecmwf_ens parquet for this storm at cycle 2026101306"},
            {"cycle": "2026101306", "ensemble": "ecmwf_aifs_ens", "reason": "not_banked", "…": "…"},
            {"cycle": "2026101306", "ensemble": "gefs_adeck", "reason": "not_published", "detail": "banked as R2 parquet tropical/tracks/ep212026/2026101306/adeck.parquet (sha256 d7e4d825…, the whole A-deck; GEFS members are told by source name (AP01-AP30), the bank's n_members counts a member column the A-deck parser leaves null); no published manifest is in the ledger (the members publish step is d091743's)"}, "…"],
 "absence": {"reason": "none_published", "detail": "no ensemble manifest is published for any cycle the bank holds: …"}}
```

`bodies/members_ep202026_2026101112_published.json` is the second layer: Simon's manifest from the recon's **synthetic** dry write.
- Served: `n_members 51`, `min_members 5`, `taus [0 … 114]`, ECMWF's CC BY 4.0 notice.
- Rachel's same-cycle manifest (1 member against a minimum of 5) and Isaias 2026101006 AIFS-ENS (7 against 10) are `too_few_members`, with their URLs.

**`/tracks` now points here.** `NOT_SERVED.ensemble_members` ends "…This API reads no R2 per request. The members' published manifests (display bucket) are indexed by GET /api/weather/tropics/members?storm_id=&cycle= (d091741)."

**And `cone_and_warnings` points at `/products`** in the same way. The brief asked only for the first. I changed the second because "not served" would otherwise sit beside an index of the same objects. It is a two-line revert if unwanted.

---

## 3. The ledger contract built against

This is recon §7's proposal; **d091740 should confirm or change it.** Each name is pinned in one place in `tropics_index.py`.

| item | value here | why |
|---|---|---|
| source | `parsed` (`DISPLAY_SOURCE`) | The CHECK admits no `display`. `parsed` is the bank's source for derived objects (`tracks:*`), so no migration is needed. If d091740 adds `display` by migration, change the one constant. |
| product | `display:{cone,ww,surge_ww,tcd,tcp}`; `display:wsp{34,50,64}_{bands,grid}`; `display:members:{ecmwf_ens,ecmwf_aifs_ens,gefs_adeck}` | recon §7 |
| storm_id / vintage_key | `<storm>` / `<ADV>`; WSP `''` / `<cycle>`; members `<storm>` / `<cycle>` | the bank's own keying of the source files |
| r2_key | `tropics/{storm}/adv{ADV}/{name}.geojson\|json`, `tropics/wsp/{cycle}/{name}.geojson`, `tropics/{storm}/{cycle}/{ensemble}/manifest.json` | recon §7. The URL is `https://display.energylake.io/` + r2_key. |
| meta (geometry) | `source_r2_key, source_sha256, geometry_types, vertices` (+ `n_features, lon_range`) | the K1 summary |
| meta (text) | `source_r2_key, source_sha256, issued_utc, transmitted_utc, forecaster, sections[{title, kind}]` | the products_text parse |
| meta (members) | `source_r2_key, source_sha256, n_members, min_members, taus` | the manifest's own (recon §4) |
| corrections | `meta.correction.revision` and a new r2_key per revision; the original row kept | 294's view and the bank's never-overwrite rule |

**The test fixture's display rows are CONSTRUCTED.** `tests/fixtures/tropics_d091741/make_display_rows.py` builds them from the recon's parsed fixtures (pantry `81ad901`, copied and sha-pinned). Each row's `source_sha256` is asserted equal to the banked ledger row's, so a constructed row can only name a file the bank holds.
- **Base layer (19 rows):** cone 032 and 058; ww 057A, 028A and 014A; surge 014A; tcd and tcp for 058 and 032; WSP bands and grid for 2026101112; WSP bands for 2026101506.
- **Second layer:** I2's r1 cone and three manifests, plus the removal of Isaias 019's TCD.

**The banked slice is real.** Every row the routes read for Isaias, Rachel, Simon and Trudy, as Postgres's `row_to_json` text through the Neon connector, cut at 13:50Z, each file's sha computed by Neon and re-checked on every load: 692 ledger rows, 550 official points, 4 storms, 4 datasets.

---

## 4. GIBS: the attribution, the probe log, three findings

### 4.1 The attribution sentence and where it was read

> We acknowledge the use of imagery provided by services from NASA's Global Imagery Browse Services (GIBS), part of NASA's Earth Science Data and Information System (ESDIS).

- **Where:** GIBS's API documentation, "Data Use Guidance and Acknowledgements": "We ask that users who make use of GIBS in their clients … add the following acknowledgment".
- **Read from:** its source repository `github.com/nasa-gibs/gibs-api-docs` at `afb15814aee74b09e672466adb731b2e34d51d8b`, `docs/index.md` (sha256 `c3cc2ba2…`). That repository is what publishes `nasa-gibs.github.io/gibs-api-docs/`.
- **Pinned** with the page: `tests/fixtures/tropics_d091741/attribution/`. G1 asserts that the served text is the page's quoted sentence.
- **Not on `gibs.earthdata.nasa.gov`:**
  - its WMS and WMTS capabilities carry `<Fees>none</Fees>`, `<AccessConstraints>none</AccessConstraints>` and no `<Attribution>`;
  - its layer metadata (`layer-metadata/v1.0/GOES-*_ABI_GeoColor.json`, pinned) carries only the title;
  - its root serves a JPEG.
- **CIRA:** the recon's memory also credited CIRA for GeoColor. No GIBS text says so, so the API adds no producer credit (`layer_credit` says so). That is the dashboard lane's call.

### 4.2 The probe log's first entries

Live, through main's own path (`probe_log.txt`):

```
2026-10-15 14:20:00 energylake.tropics.ground {"absent_before_frame": ["…14:10:00Z", "…14:00:00Z", "…13:50:00Z"], "age_min": 39.9, "frame_utc": "2026-10-15T13:40:00+00:00", "layer": "GOES-East_ABI_GeoColor", "probed_utc": "2026-10-15T14:19:56.574339+00:00", "recent_present": 12, "refusal": null, "slots_asked": 4}
2026-10-15 14:20:00 energylake.tropics.ground {"absent_before_frame": [], "age_min": null, "frame_utc": null, "layer": "GOES-West_ABI_GeoColor", "probed_utc": "2026-10-15T14:20:00.032925+00:00", "recent_present": 0, "refusal": "gibs_unreachable", "slots_asked": 1}      <- the bug, §4.3
2026-10-15 14:20:39 energylake.tropics.ground {"absent_before_frame": ["…14:20:00Z", "…14:10:00Z", "…14:00:00Z", "…13:50:00Z"], "age_min": 40.6, "frame_utc": "2026-10-15T13:40:00+00:00", "layer": "GOES-East_ABI_GeoColor", "probed_utc": "2026-10-15T14:20:36.583792+00:00", "recent_present": 12, "refusal": null, "slots_asked": 5}
2026-10-15 14:20:41 energylake.tropics.ground {"absent_before_frame": ["…14:20:00Z", "…14:10:00Z", "…14:00:00Z", "…13:50:00Z"], "age_min": 40.7, "frame_utc": "2026-10-15T13:40:00+00:00", "layer": "GOES-West_ABI_GeoColor", "probed_utc": "2026-10-15T14:20:39.161569+00:00", "recent_present": 12, "refusal": null, "slots_asked": 5}
```

And this lane's z0 recordings (`record_gibs_z0.py`; fixtures for G1):

| probed (UTC) | East frame, age | West frame, age | note |
|---|---|---|---|
| 13:54:42 | 13:10, 44.7 min | 13:20, 34.7 min | |
| 13:59:23 | 13:30, 29.4 min | 13:30, 29.4 min | East **13:20 is a hole below the frame** |
| 14:20:00 | 13:40, 40.0 min | 13:40, 40.0 min | East 13:20 now present: backfilled |

With the recon's six runs (13:02–13:27Z, 31.9–47.4 min), that is **one time of day, again** (13:02–14:20Z). The log line is the mechanism for the other two: in production one line per layer every 5 minutes is the measurement.

### 4.3 Three findings

1. **`layer-time-actual`.** A 200 tile carries `layer-time-actual` (and `layer-identifier-actual: GOES-East_ABI_GeoColor_v0_NRT`), GIBS's statement of the time it served. The probe requires it to equal the slot when present. That turns the recon's "an off-grid time snaps" into a check: a snapped 200 is absent at that slot.
2. **Frames land late and out of order.** The newest frame is not "the latest slot with all older slots present"; `recent_present` lists holes as holes. At 13:59Z East had 13:30 and not 13:20. The dashboard's player should step through `recent_present`, not assume 10-minute steps.
3. **One httpx client kept across event loops failed the second probe** ("Event loop is closed", 14:20:00Z, West). Uvicorn's single loop would hide it; a worker restart, or the TestClient, does not. Each probe now opens and closes its own client: ~16 GETs per layer per 5 minutes. Guarded by `test_G1_the_service_path_probes_through_httpx_on_each_requests_own_loop`, with a transport bound to its first loop, and by rehearsal break 19.

---

## 5. The EXPLAINs

Full receipts: `docs/receipts/tropics-api-index-d091741/neon_plans.txt`, verbatim, pinned by X1.
- **Taken:** on Neon, between 14:00Z and 14:15Z (not stamped per statement), `EXPLAIN (ANALYZE, BUFFERS)`.
- **Statement text:** `explains.json`, psycopg's mogrify of the routes' own statements and parameter builders.
- **The ledger at the time:** 7,029 rows and **0 `display:*` rows**. The display rows are one more match on the same Index Cond.

| statement (params) | plan on `tropical_file_vintage` / points | rows | buffers | exec |
|---|---|---:|---:|---:|
| ENTRY_NEWEST_SQL (ep202026) | Index Scan using **tfv_identity**: `source, product, storm_id`; top-N sort | 79 | 73 hit | 0.280 ms |
| ENTRY_NEWEST_SQL (ep182026) | Index Scan using **tfv_identity** | 38 | 39 hit | 0.219 ms |
| ENTRY_SQL (ep182026, `058:%`) | Index Scan using **tfv_identity**, Index Cond incl. `vintage_key >= '058:' AND < '058;'` | 4 | 6 hit | 0.088 ms |
| FILES_SQL products (ep202026, 032) | Index Scan using **tfv_identity**, all four columns; the original's probe "never executed" | 1 | 13 hit | 0.100 ms |
| FILES_SQL wsp ('', 2026101506) | Index Scan using **tfv_identity** | 2 | 9 hit | 0.100 ms |
| FILES_SQL text (ep202026, 032) | Index Scan using **tfv_identity** | 2 | 9 hit | 0.100 ms |
| MEMBERS_SQL (ep202026, all) | Index Scan using **tfv_identity**: `source, product = ANY, storm_id` | 62 | 67 hit | 0.375 ms |
| MEMBERS_SQL (ep202026, 2026101112) | Index Scan using **tfv_identity**, + `vintage_key` | 3 | 7 hit | 0.101 ms |
| MEMBERS_SQL (ep212026, all) | Index Scan using **tfv_identity** | 9 | 13 hit | 0.154 ms |
| ADVISORY_AT_SQL (ep202026, 032) | Index Scan Backward using `uniq_tropical_track_points` + `ttp_revised` anti-join | 8 | 14 hit, 4 read | 2.676 ms |
| ADVISORY_NEWEST_SQL (fallback) | `uniq_tropical_track_points` + `ttp_revised` | 8 | 21 hit | 0.133 ms |
| STORM_SQL (d091673's) | Seq Scan on **tropical_storms** (4 rows, 1 buffer) | 1 | 1 hit | 0.033 ms |

**On the plans:**
- **No seq scan of `tropical_file_vintage` or `tropical_track_points` anywhere.**
- **The in-force view's `DISTINCT ON` is fed by the index's own order** (Incremental Sort, presorted on the index's leading key columns). This satisfies CLAUDE.md's d091551 note: the view reads only the named identities' rows, not every row.
- **The ENTRY_NEWEST read sorts one storm's entries** (79 for Simon, 38 for Rachel) for one row. That is bounded by a storm's life; an index on `(…, issued_ts)` would make it one probe, but is not worth adding at 0.3 ms.
- **Locally** (`test_X1_locally_…`): on the fixture ledger padded to Neon's 7,029 rows, every ledger read is an index read through `tfv_identity`. At this size the planner sometimes picks the bitmap form; the test accepts either, never a seq scan.
- **No index or migration was added** (`test_X1_no_index_was_added`).

---

## 6. Tests

### 6.1 The cells

New module: `tests/test_tropics_index_d091741.py`, 87 tests.

| cell | what it asserts | tests |
|---|---|---:|
| F | Every pinned fixture file's sha; the banked slice's Neon shas; the display rows equal their generator's; every display row names a banked source by sha; the filler names nothing a route reads. | 4 |
| G1 | The recon's six runs give its own newest present slot and age (32.6/32.6, 41.9/31.9, 47.4/37.4). This lane's z0 runs give the first present slot from now, the age, and `recent_present` with holes kept as holes. Never `default` (every URL asked and every template). 18 misses → `no_frame_in_window`. Transport error or 503 → `gibs_unreachable`. The tile fixtures classify (200 PNG present; 404 absent; snapped 200 absent; 503 error). The memo answers in 4 min without a probe and probes again after 5. East for al, West for ep and cp (shared memo). The MapLibre raster shape. One log line per probe. GIBS's sentence. The service path on two loops. Bad basin → 400. | 24 |
| I1 | Six objects equal the fixture (sha, bytes, vertices, geometry types, source key and sha). Rachel 058: ww `not_issued` on NHC's word. Simon 032: surge `basin_has_none`. The WSP cycle is the synoptic time (2026101112, 2026101506) and its objects equal K1. Banked-not-published (Simon 031, Isaias 014A cone) → `not_published`. An earlier advisory's link → `not_issued` (Isaias 013A surge, 015 ww). The default is NHC's newest (Isaias 020, Simon 032, Trudy 008). Unknown storm and advisory → 404; bad params → 400. Every URL is the display bucket's. | 14 |
| I2 | The copy in force is r1, with `revision`, `corrected` and the original's url and sha stated; the live layer still serves r0; the ledger holds both copies and the view one. | 2 |
| T1 | Section kinds, titles, both times and forecaster equal T1 for Rachel 058 and Simon 032; `not_banked` (Isaias 019, constructed); `not_issued` for 057A's TCD; Isaias 020 `not_published`. | 4 |
| M1 | Every banked (cycle, ensemble) of Isaias, Rachel and Simon is `not_published`, every other one `not_banked`, counts equal the ledger. Trudy: ECMWF `not_banked` at every cycle, A-deck `not_published`. The `/tracks` wording points here. A published manifest is served; two too-small ones are `too_few_members` on the manifest's own minimum. An unseen cycle is `not_banked` ×3. | 7 |
| X1 | The Neon receipts: no seq scan of either table, `tfv_identity` on every ledger read, each under 5 ms, receipts are the code's statements. Locally ×7 statements × 2 layers. No index added. | 16 |
| C | The statement timeout is the first statement of each build; a second call is a memo hit with no read; every ledger read is of the in-force view (the one bare read is the corrected original's). | 5 |
| V1 | Each of d091673's 11 vectors minus the two pointers is main's body byte for byte (sha pinned at 265f9fb). | 11 |

### 6.2 Red on main, then green

`docs/receipts/tropics-api-index-d091741/red_on_main.txt`: the final module, its loader, fixtures and plan receipts, run in a worktree of main at `265f9fb` with main's code.
- **On main: 81 of 87 red.**
- **The six that pass on main** check fixtures only (F ×4, I2's ledger/view check, X1's no-index check). Every test of the routes' behaviour is red.
- **On the branch:** 87 of 87 green.

### 6.3 Mutation rehearsal

`tests/rehearse_tropics_index_d091741.py` → `reds.txt`: **20 of 20 red**, clean suite green before and after, tree sha256 `fca4067e…` → `fca4067e…` (byte for byte).

| # | break | red in |
|---|---|---|
| 1–2 | **`default` bound** (the template, then the probe's own tile) | G1 |
| 3 | **the in-force rule dropped** (every banked copy read) | I2 |
| 4 | **the memo removed** (G1 probes twice) | G1 |
| 5 | `layer-time-actual` ignored | G1 |
| 6 | an errored slot listed present | G1 |
| 7 | the window cut to 12 | G1 |
| 8 | ep drawn from East | G1 |
| 9 | surge sought in every basin | I1 |
| 10 | not_issued without NHC's word | I1/T1 |
| 11 | WSP cycle from the advisory time | I1 |
| 12 | default = newest official forecast | I1/T1 |
| 13 | the original's url dropped | I2 |
| 14 | the two times collapsed | T1 |
| 15 | not_published served as not_banked | M1 |
| 16 | an API floor of 5 for too_few_members | M1 (Isaias 7 vs 10) |
| 17 | `/tracks` wording reverted | M1/V1 |
| 18 | the ledger read names no product or storm | X1/I1 |
| 19 | one httpx client kept across loops | G1 service path |
| 20 | no statement timeout | C |

The first version of break 16 (an API floor of 2) **was missed**: Rachel's 1 member is "too few" under either rule. Isaias's 7-against-10 manifest was added so the test tells the manifest's minimum from any floor the API might make up.

### 6.4 The suite, before and after

| | passed | failed | total |
|---|---:|---:|---:|
| main `265f9fb` (before) | 2,965 | 2 | 2,967 |
| this branch (after) | **3,052** | 2 | 3,054 (+87: this lane's module) |

The 2 failures, identical on both:
- `tests/test_asset_runs_d091667.py::test_H8_a_rebanked_vector_is_mains_plus_the_one_key[…asset_wind_57514.json…]`
- `tests/test_asset_runs_d091667.py::test_H8_a_rebanked_vector_is_mains_plus_the_one_key[…body_wind_ciso_n28.json…]`

They fail on main before this lane: their "main copy" already carries `attribution`. Not touched here.

The first full run on the branch also failed `tests/test_press_d091689.py::test_A8_no_existing_route_changed`, because this lane added lines to main.py. It is repaired as §7 says, and green in the run above.

The tropics suites alone, after: `test_tropics_d091673.py` + `test_tropics_rows_in_force_d091682.py`, **115 passed**.

---

## 7. Changes to existing files, and why each

| file | change |
|---|---|
| `main.py` | **Lines added only**, after `/odds` in the tropics section: 4 routes, 4 memos, the GIBS fetch, the advisory reads. d091673's `test_R_no_existing_route_or_memo_changed` (main only gains lines since `149e314`) holds. |
| `tropics.py` | `NOT_SERVED.ensemble_members` points at `/members` (the brief); `cone_and_warnings` at `/products` (§2.5). Each keeps "reads no R2"; d091673's `"R2 parquet"` assertion holds. |
| `tests/fixtures/tropics_d091673/bodies/*.json` (11) | Re-banked by substituting exactly the two strings (`rebank_vectors.py`); d091673's V tests prove the served bodies equal them; V1 proves each minus the two equals main's. |
| `docs/receipts/tropics-api-d091673/sizes.json` | `raw` and `gzip6` re-measured: every entry +246 raw bytes, the two pointers exactly (`rebank_sizes.py`); `local_build_ms` untouched. d091673's T10 pins sizes to 16 bytes. |
| `tests/test_tropics_rows_in_force_d091682.py` | **One line** in R6: revert the two pointers before hashing against d091673's shas (`load_bank_d091741.revert_not_served`, which refuses a body whose statements are not this lane's). Plus its import. |
| `tests/test_press_d091689.py` | **A8 repaired** (`test_A8_no_existing_route_changed`). It rebuilt main.py at its base `ce2522f` by subtracting the press section from the *working* main.py, so any later lane that adds a line goes red, and this lane's did. It now reads main.py at the press lane's own commit `58d0bf3`, the only commit to touch main.py between `ce2522f` and `265f9fb`. That proves the press lane's diff exactly as before; the live check of the press route list is unchanged. The same repair d091682 made to d091673's test (ruling 3 there). It passes on main's code and on this branch. |

**New:**
- `tropics_index.py`
- `tests/test_tropics_index_d091741.py`, `tests/load_bank_d091741.py`, `tests/rehearse_tropics_index_d091741.py`
- `tests/fixtures/tropics_d091741/` (bank, recon copies, constructed rows and their generator, GIBS recordings and tiles, attribution, `manifest.json`)
- `docs/receipts/tropics-api-index-d091741/`:
  - `neon_plans.txt`, `explains.json`, `explains_sql.py`
  - `red_on_main.txt`, `reds.txt`, `probe_log.txt`
  - `record_gibs_z0.py`, `rebank_vectors.py`, `rebank_sizes.py`, `write_example_bodies.py`, `bodies/`

---

## 8. What this brief got wrong

1. **The ledger index.** "Indexed by `(dataset, source, product, storm_id, vintage_key)`": it is `tfv_identity (source, product, storm_id, vintage_key)`. No ledger index carries `dataset`, and no read here needs it.
2. **d091740's spec path** (`claude/cc_spec_2026_10_15_tropics_publish_products_d091740.md`) is in no repository this lane can read. d091740 has not published: 0 `display:*` rows, display bucket 404.
3. **Lane numbers drifted from the recon.**
   - Recon §7 named **d091738** the "serve" lane (these routes), **d091740** the members publish and **d091741** the members **draw** (dashboard).
   - This brief makes d091740 the products publish, d091741 the API index and d091743 the members publish.
   - The docstrings and bodies follow the brief.
4. **"advisory defaults to the row in force for the newest advisory"** needs "newest" to be NHC's, by its CurrentStorms entries, **not the newest official forecast.** Isaias 016–020 are post-tropical and text-only (no GIS, no forecast points); "newest official" serves 015 on `/text` while NHC's last word is 020. Without an official forecast there is no synoptic time, so the WSP block says `no_synoptic_time`.
5. **`not_issued` is not visible in the files.** The bank writes no row for a product NHC didn't post. It is visible only in `storm_entry.meta.refs`. The brief's examples hold:
   - **Rachel 058:** two of its four entries still linked **057A's** ww; its newest links none.
   - **Simon 032:** links none.
6. **`/text`'s reasons need `not_issued` too.** NHC issues no TCD with an intermediate advisory (Rachel 057A's entry links 057's), and calling that `not_banked` would claim a gap the bank doesn't have.
7. **`too_few_members` cannot be the API's count.** The API reads no parquet. It is the published manifest's `n_members` against its own `min_members` (D-09-09-M). And "not_published for every banked cycle today" holds only because the API applies no floor of its own: Rachel 2026101112 holds 1 member.
8. **"not_banked for Trudy"** is true of ECMWF only. Trudy's A-deck is banked at 9 cycles, so `gefs_adeck` is `not_published`.
9. **`recent_present [≤12]` and "stops at the first 200"** conflict. Resolved: the frame search stops at the first present slot; then the frame's 11 older slots are asked together (≤ 29 GETs per probe, typically ~16).
10. **"Every route: a statement timeout"**: `/ground` reads no table. Its bounds are a 4 s per-tile timeout, a 20 s search budget, and a 30 s build backstop (503).
11. **The attribution sentence is not on any GIBS-hosted page.** It is in GIBS's docs on GitHub (§4.1). The recon's memory dropped "NASA's" and the expansion of ESDIS, and credited CIRA, which no GIBS text does.
12. **"Three times of day"** cannot come out of one session: 13:02–14:20Z is one time of day. The probe's log line is now the instrument.
13. **The existing tests pinned bodies byte for byte.** Pointing `not_served` at `/members` changes all 11 d091673 vectors and every pinned body size; that re-bank and one line in d091682's R6 are part of this lane (§7).
14. **The source CHECK has no room for a display source** (`parsed` is used); d091740 decides.
15. **X1's "index scans"**: on a small local ledger the planner picks a bitmap scan on `tfv_identity`, or a seq scan when the table is tiny. The local test pads to Neon's size; Neon's own plans are plain index scans.

---

## 9. For the next lanes

- **d091740 (publish):** confirm or change §3: the source, product names, r2_key family, meta keys, and the correction convention (a new r2_key per revision, `meta.correction.revision`, the original kept).
  - Publish `wsp*_grid` for every cycle you publish bands for. The fixture has 2026101506's grid `not_published` only because the recon parsed 2026101112's alone.
- **d091743 (members publish):** state `n_members`, `min_members` and `taus` on the ledger row's meta, as the manifest has them. `/members` reads nothing else.
- **Dashboard:**
  - Bind `layers[0].template`; step the player through `recent_present` (holes are real).
  - Print the receipt line from `frame_utc` and `age_min` at `probed_utc`. `age_min` is at the probe, so add `cache.age_seconds` for "now".
  - Show the GIBS sentence as given.
  - Text-only advisories (Isaias 016–020) have no cone, by NHC's word.
