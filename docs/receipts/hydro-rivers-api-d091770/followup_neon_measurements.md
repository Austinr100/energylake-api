# The follow-up's live reads (2026-10-17, read-only)

Every figure in the handback's **Corrections (follow-up, 10-17)** comes from one of the
five statements below, run through the Neon MCP against project `fancy-block-96153928`,
database `neondb`, default branch. **SELECT only; nothing was written to Neon, R2, the
pantry or the dashboard**, and no route or test reads live Neon — the suite runs on the
sha-pinned 11:30Z slice in `tests/fixtures/hydro_rivers_d091770/`.

## M1 — the layers (C1): 15 layer codes = 12 reach walks + 3 crosswalks

```sql
SELECT layer_code, count(*) AS n, count(*) FILTER (WHERE role='derived') AS derived
FROM geo_layer_objects GROUP BY layer_code ORDER BY layer_code;
```

12 `nhdplus_hr_*`: `blue_river_blu`, `coast_fork_cot`, `columbia_gcl_bon`,
`fall_creek_fal`, `long_tom_frn`, `north_santiam_det`, `row_river_dor`,
`sacramento_shasta_delta`, `santiam_gpr`, `sf_mckenzie_cgr`, `snake_lwg_columbia`,
`willamette_hcr_columbia`. 3 `nwm_crosswalk_*`: `columbia`, `sacramento`, `snake`.
Nine of the twelve are Willamette-basin walks, which is why `?river=willamette` reaches
one of nine.

Publish rows at the time of this read:

```sql
SELECT CASE WHEN g.layer_code LIKE 'nwm_crosswalk%' THEN 'crosswalk' ELSE 'reach walk' END AS kind,
       count(*) AS publish_rows, count(DISTINCT g.layer_code) AS layer_codes
FROM geo_display_objects d JOIN geo_layer_objects g ON g.object_id = d.object_id
GROUP BY 1;
```

| kind | publish rows | layer codes |
| --- | --- | --- |
| reach walk | 12 | 12 |
| crosswalk | 4 | 3 |

So `/index` serves **12** entries today, one per published walk.

## M2 — the records per walk (C1, C2): the Columbia carries 11 projects

```sql
WITH keys AS (SELECT DISTINCT kind, source_code, source_key FROM geo_records),
newest AS (SELECT r.* FROM keys k CROSS JOIN LATERAL (
   SELECT g.* FROM geo_records g
   WHERE g.kind=k.kind AND g.source_code=k.source_code AND g.source_key=k.source_key
   ORDER BY g.fetched_at DESC, g.record_id DESC LIMIT 1) r)
SELECT COALESCE(attrs->'_bank'->'anchor'->>'reach', attrs->'_bank'->>'reach') AS walk,
       kind, count(*), string_agg(source_key, ',' ORDER BY source_key),
       string_agg(DISTINCT COALESCE(attrs->'_bank'->>'river','(none)'), '|')
FROM newest GROUP BY 1,2 ORDER BY 1,2;
```

`nhdplus_hr_columbia_gcl_bon`: **11 `cwms_project`** (BON CHJ GCL JDA MCN PRD RIS RRH TDA
WAN WEL) and 3 gauges. `nhdplus_hr_snake_lwg_columbia`: 4 projects (IHR LGS LMN LWG) and
1 gauge. `nhdplus_hr_sacramento_shasta_delta`: 10 gauges, no project. Each Willamette
walk carries 1–3 projects and an `attrs._bank.river` equal to **its own walk key**, so no
record anywhere says the nine are one river.

## M3 — the anchor km order (C2)

```sql
-- same `newest` CTE
SELECT walk, source_key, (attrs->'_bank'->'anchor'->'km_from_reach_start'->>0)::numeric AS km_lo
FROM newest WHERE kind='cwms_project' AND walk IN (…) ORDER BY walk, km_lo NULLS LAST;
```

Columbia: GCL 0.0, CHJ 82.045, **WEL 128.528, RRH 196.352, RIS 228.457, WAN 291.039,
PRD 320.72**, MCN 489.862, JDA 614.028, TDA 653.656, BON 727.887.
Snake: LWG 0.0, LGS 59.627, LMN 105.761, IHR 154.068.

The pantry's d091752 dry run gates **GCL, CHJ, MCN, JDA, TDA, BON** on the Columbia (the
crosswalk's placed set; `tests/fixtures/hydro_rivers_d091770/pantry_d091752/`) and
**LWG, LGS, LMN, IHR** on the Snake. So the anchor km agrees with the crosswalk's order on
every shared item, and the five in bold are items the pantry does not have at all.

## M4 — which Columbia projects publish a release (C3)

```sql
SELECT d.series, v.present IS NOT NULL AS has_outflow
FROM (VALUES ('BON'),('CHJ'),('GCL'),('JDA'),('MCN'),('PRD'),('RIS'),('RRH'),('TDA'),('WAN'),('WEL')) AS d(series)
LEFT JOIN LATERAL (SELECT 1 AS present FROM timeseries_values t
  WHERE t.dataset = 'cwms_outflow_hourly' AND t.series = d.series
  ORDER BY t.ts DESC LIMIT 1) v ON TRUE ORDER BY d.series;
```

**MCN is the only false.** WAN and BON are true — the banked slice's "no banked release"
for those two is the slice's, not the bank's (handback C3). One `LIMIT 1` per series: an
existence read, never a scan.

## M5 — the Columbia crosswalk's publish state (C7)

```sql
SELECT g.object_id, g.layer_code, g.fetched_at, d.display_key, d.published_at
FROM geo_layer_objects g LEFT JOIN geo_display_objects d ON d.object_id = g.object_id
WHERE g.layer_code LIKE 'nwm_crosswalk%' ORDER BY g.layer_code, g.object_id;
```

Object 9 published 2026-10-17T11:05:11Z; **object 32 (banked 11:27Z) published
2026-10-17T16:05:44Z** — after the lane's 11:30Z cut. The fixture still holds the pending
case and the rule it proves is unchanged.
