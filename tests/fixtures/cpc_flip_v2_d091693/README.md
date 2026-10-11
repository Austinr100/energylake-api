# cpc_flip_v2_d091693 — Neon's CPC curve rows, both method versions, read 2026-10-11 13:46:48–13:47:01Z

Each `*.json.gz` is gzip (level 9, mtime 0) of the exact `t` text one read-only
statement returned on Neon production (`fancy-block-96153928`, default branch),
plus a newline. `manifest.psv` is `file | rows | sha-256 | length | read_at`, the
sha-256 and length computed by Neon over that same text (`encode(sha256(convert_to(t,
'UTF8')), 'hex')`, `length(t)`); `tests/load_bank_d091693.py` checks each file
against them before it uses a row. Nothing here is constructed or edited.

| file | rows |
|---|---|
| verdicts_v1.json, verdicts_v2.json | every `cpc_curve_verdicts` row of the version, every column but `verdict_id` |
| curves_{610temp,814temp}_{v1,v2}.json | every column but `curve_row_id` of the product and version's `cpc_outlook_curves` rows (a) at KSAN, KDEN, San Jose (USW00023293), pnw at both weightings and caiso_sp15_proxy at load_share_365d on every issuance 2026-09-24 .. 2026-10-10, and (b) at every place on the version's newest issuance (v1 2026-10-09, v2 2026-10-10) |

The statements, verbatim:

verdicts_v1.json:
```sql
WITH t AS (SELECT coalesce(jsonb_agg(to_jsonb(v) - 'verdict_id' ORDER BY v.backtest_version, v.product, v.place_kind, v.place, v.weighting, v.season, v.strength), '[]')::text AS t FROM cpc_curve_verdicts v WHERE v.method_version = 'cpc_curves_v1') SELECT now() AS read_at, length(t) AS len, encode(sha256(convert_to(t, 'UTF8')), 'hex') AS sha, t FROM t
```

verdicts_v2.json:
```sql
WITH t AS (SELECT coalesce(jsonb_agg(to_jsonb(v) - 'verdict_id' ORDER BY v.backtest_version, v.product, v.place_kind, v.place, v.weighting, v.season, v.strength), '[]')::text AS t FROM cpc_curve_verdicts v WHERE v.method_version = 'cpc_curves_v2') SELECT now() AS read_at, length(t) AS len, encode(sha256(convert_to(t, 'UTF8')), 'hex') AS sha, t FROM t
```

curves_610temp_v1.json:
```sql
WITH t AS (SELECT coalesce(jsonb_agg(to_jsonb(c) - 'curve_row_id' ORDER BY c.issued_date, c.place_kind, c.place, c.weighting, c.day_index NULLS FIRST), '[]')::text AS t FROM cpc_outlook_curves c WHERE c.method_version = 'cpc_curves_v1' AND c.product = '610temp' AND ((c.issued_date BETWEEN '2026-09-24' AND '2026-10-10' AND (c.place_kind, c.place, c.weighting) IN (('station','USW00023188',''),('station','USW00003017',''),('station','USW00023293',''),('region','pnw','population'),('region','pnw','load_share_365d'),('region','caiso_sp15_proxy','load_share_365d'))) OR c.issued_date = '2026-10-09')) SELECT now() AS read_at, length(t) AS len, encode(sha256(convert_to(t, 'UTF8')), 'hex') AS sha, t FROM t
```

curves_814temp_v1.json:
```sql
WITH t AS (SELECT coalesce(jsonb_agg(to_jsonb(c) - 'curve_row_id' ORDER BY c.issued_date, c.place_kind, c.place, c.weighting, c.day_index NULLS FIRST), '[]')::text AS t FROM cpc_outlook_curves c WHERE c.method_version = 'cpc_curves_v1' AND c.product = '814temp' AND ((c.issued_date BETWEEN '2026-09-24' AND '2026-10-10' AND (c.place_kind, c.place, c.weighting) IN (('station','USW00023188',''),('station','USW00003017',''),('station','USW00023293',''),('region','pnw','population'),('region','pnw','load_share_365d'),('region','caiso_sp15_proxy','load_share_365d'))) OR c.issued_date = '2026-10-09')) SELECT now() AS read_at, length(t) AS len, encode(sha256(convert_to(t, 'UTF8')), 'hex') AS sha, t FROM t
```

curves_610temp_v2.json:
```sql
WITH t AS (SELECT coalesce(jsonb_agg(to_jsonb(c) - 'curve_row_id' ORDER BY c.issued_date, c.place_kind, c.place, c.weighting, c.day_index NULLS FIRST), '[]')::text AS t FROM cpc_outlook_curves c WHERE c.method_version = 'cpc_curves_v2' AND c.product = '610temp' AND ((c.issued_date BETWEEN '2026-09-24' AND '2026-10-10' AND (c.place_kind, c.place, c.weighting) IN (('station','USW00023188',''),('station','USW00003017',''),('station','USW00023293',''),('region','pnw','population'),('region','pnw','load_share_365d'),('region','caiso_sp15_proxy','load_share_365d'))) OR c.issued_date = '2026-10-10')) SELECT now() AS read_at, length(t) AS len, encode(sha256(convert_to(t, 'UTF8')), 'hex') AS sha, t FROM t
```

curves_814temp_v2.json:
```sql
WITH t AS (SELECT coalesce(jsonb_agg(to_jsonb(c) - 'curve_row_id' ORDER BY c.issued_date, c.place_kind, c.place, c.weighting, c.day_index NULLS FIRST), '[]')::text AS t FROM cpc_outlook_curves c WHERE c.method_version = 'cpc_curves_v2' AND c.product = '814temp' AND ((c.issued_date BETWEEN '2026-09-24' AND '2026-10-10' AND (c.place_kind, c.place, c.weighting) IN (('station','USW00023188',''),('station','USW00003017',''),('station','USW00023293',''),('region','pnw','population'),('region','pnw','load_share_365d'),('region','caiso_sp15_proxy','load_share_365d'))) OR c.issued_date = '2026-10-10')) SELECT now() AS read_at, length(t) AS len, encode(sha256(convert_to(t, 'UTF8')), 'hex') AS sha, t FROM t
```
