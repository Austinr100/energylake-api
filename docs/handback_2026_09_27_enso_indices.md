# Handback — `GET /api/enso/indices` (lane d091493)

**Branch:** `claude/enso-indices-d091493-0bpkf5`, cut from `origin/main` @ `27dd93c`. The branch exists only; there is no PR, no merge and no deploy.
**Spec:** pantry `docs/cc_spec_2026_09_27_enso_pin_and_odds.md` §0, D-09-25-38, D-09-25-43 and §2. **I did not read the spec.** `Austinr100/energylake-pantry` is not attached to this session, so the lane prompt served as the contract (see "What the spec got wrong", item 1).

## Route

```
GET /api/enso/indices?index=oni,roni      (default: both)
```

| case | status | headers |
|---|---|---|
| served | 200 `{"indices": {"oni": {...}, "roni": {...}}}` | `ETag: W/"<16 hex>"`, `Cache-Control: max-age=3600` |
| `If-None-Match` weak-matches | 304, empty body | same ETag + Cache-Control |
| index not `oni` / `roni` (includes empty tokens and `ONI`) | 400 `unknown index 'x'; allowed: oni, roni` | none |
| a requested index has zero rows | 503 `no rows banked for roni (cpc_roni_monthly/roni)` | none; not memoised |
| pool / DB error | 503 `db unavailable: …` | none; not memoised |

Each block has these keys, in this order: `dataset, series, n, n_null, first, last, last_ingested, values`. `values` is `[["YYYY-MM", float], …]` in ascending order. The month is the season's centre month, taken from `ts` in UTC; a `ts` that is not a UTC month start raises. A null value is left out of `values` and counted in `n_null`. `first` and `last` are the first and last months actually served. `last_ingested` is `max(ingested_ts)` as ISO-Z. Keys always come out in the order oni, roni, whatever order the query used, and a repeated index is served once.

The ETag is `W/"` + the first 16 hex characters of the sha256 of `json.dumps({index: values}, separators=(",",":"), sort_keys=True)` + `"`. It depends only on the values, never on a clock. `last_ingested` is deliberately left out of the hash, so a re-ingest that writes identical values keeps its 304. Each index set gets its own tag.

The memo lasts 60 s in-process and is keyed on the canonical index tuple, so `?index=roni,oni` and the default share one entry. The route uses one `_pool.connection()`, so the pre-ping on checkout still runs, and one `SELECT` per requested index.

## Module

* `enso_indices.py` follows the shape of `enso_catalog.py`: `INDICES`, `SERIES_SQL`, `parse_indices`, `build_series`, `build_payload`, `etag`, `etag_matches`.
  * `Decimal` is converted to `float` explicitly in `_value`.
  * `str`, `bool` and other non-numeric types are refused by name.
  * `NaN` and `±inf` are refused.
* `main.py` has the route directly after `/api/enso/catalog`, reusing `_ENSO_MEMO_TTL` and `_ENSO_CACHE_CONTROL`.
  * The response is a plain `JSONResponse`, not `jsonable_encoder`, so a Decimal that slips through raises instead of being coerced. Test I4 covers this.
  * The route is also listed in the module docstring.
* `README.md` has a new "ENSO indices" section.

## Tests — `tests/test_enso_indices.py` (fake `_TsPool`, no DB)

The fake reads the SQL the way psycopg would: `value` comes back as `Decimal`, `ts` is timezone-aware, the `dataset` / `series` filters apply only when the WHERE clause has them, rows are stored out of time order, and a neighbouring series (`oni_total`) sits in the same table.

The red run used this test file against `origin/main`. All 30 tests error in the autouse fixture, because `main` has no `_enso_indices_cache` and no route. Adding `enso_indices.py` alone leaves the result unchanged: all 30 still error. The green run is on the branch.

| # | test | red (main) | green |
|---|---|---|---|
| I1 | `test_i1_shape_ascending_month_strings`: key order, dataset/series, n/n_null/first/last/last_ingested, ascending, `YYYY-MM`, float type, neighbour series excluded | error | pass |
| I1 | `test_i1_ascends_even_if_the_sql_order_is_dropped` | error | pass |
| I1 | `test_i1_season_centre_month_is_utc_and_must_be_a_month_start` | error | pass |
| I2 | `test_i2_etag_stable_and_content_addressed`: re-ingest keeps the tag | error | pass |
| I2 | `test_i2_etag_changes_with_one_value` | error | pass |
| I2 | `test_i2_etag_differs_per_index_set` | error | pass |
| I2 | `test_i2_304_on_match_with_headers`: weak/strong/listed match; a stale tag gets 200 | error | pass |
| I2 | `test_i2_etag_is_sha256_of_canonical_values` | error | pass |
| I3 | `test_i3_unknown_index_400_names_the_allowed_set` ×5 (`nino34`, `oni,nino34`, empty, `oni,`, `ONI`); no DB checkout | error | pass |
| I3 | `test_i3_single_and_reordered_index` ×5; reads only the requested series | error | pass |
| I4 | `test_i4_decimal_to_float_and_nulls_counted`: a trailing null is not `last` | error | pass |
| I4 | `test_i4_build_series_types`: Decimal→float, str refused, NaN refused | error | pass |
| I4 | `test_i4_stray_decimal_fails_loud_not_coerced`: a leaked Decimal raises `TypeError` at JSONResponse | error | pass |
| I5 | `test_i5_zero_rows_503_names_it_and_is_not_memoised` | error | pass |
| I5 | `test_i5_db_unavailable_503` | error | pass |
| I5 | `test_i5_memo_keyed_on_index_set_and_expires` | error | pass |
| I6 | `test_i6_route_is_not_cors_exempt` | error | pass |
| I6 | `test_i6_cors_echoes_trusted_origins` ×3 (apex, www, preview): GET 200 + 304 + preflight echo the origin with credentials | error | pass |
| I6 | `test_i6_foreign_origin_gets_no_acao` | error | pass |
| — | `test_route_placement_and_docs`: registered once, directly after the catalog; README and docstring rows | error | pass |

The I6 tests check the real `main.app` middleware entry: it is `SkyExemptCORSMiddleware`, its `allow_origins` is the same object as `main.ALLOWED_ORIGINS`, and it has the preview regex, credentials and GET. The tests then load `https://energylake.io` and `https://www.energylake.io` into that list and restore it afterwards. This is needed because the test environment's `ALLOWED_ORIGINS` is the localhost default.

The full suite, `python -m pytest tests/ -q`, gives **1961 passed** (from 1931 on main, plus these 30).

## Receipt — `docs/receipts/enso-indices-d091493/`

* **`indices_2026_09_27.json`:** 32,329 bytes, 200 status.
  * **sha256 `8dafd49a5e4c2217210fefe02edd2dcc67a45d1f6dc932d14742dd17487c44c0`**, also stored in `indices_2026_09_27.json.sha256`.
  * `ETag: W/"5e1fe1eb773bf3d4"`.
  * oni: 919 values, n_null 0, 1950-01 (−1.32) to 2026-07 (1.8), last_ingested 2026-09-27T18:40:09.322000Z.
  * roni: 919 values, n_null 0, 1950-01 (−1.19) to 2026-07 (1.36), last_ingested 2026-09-27T18:40:12.772000Z.
* **`source_{oni,roni}_2026_09_27.txt`:** the production rows exactly as Postgres produced them, one read-only `SELECT` per index through the Neon console on project `fancy-block-96153928`, default branch. Postgres computed the sha256 of each text server-side and the local copies match byte for byte:
  * oni `f42aa854…372416`
  * roni `ec794245…b30f5`
* **`bank.py`:** checks both shas, rebuilds the rows the way psycopg returns them (Decimal values, timestamptz), and serves them through the **real** route and module on a stand-in pool. It is deterministic: two runs gave the same sha. To reproduce: `python docs/receipts/enso-indices-d091493/bank.py`.

These facts were measured in production at pull time:
* 919 rows per index.
* 0 nulls.
* 0 `ts` values off a UTC month start.
* 919 distinct months.
* `value` is `numeric` at scale 6 (for example `1.800000`).
* Every row in each series has the same `ingested_ts`: a single ingest on 2026-09-27.

## What the spec got wrong (or left open)

1. **The spec was not reachable.** The pantry repo is not attached to this session. §0, D-09-25-38, D-09-25-43 and §2 were not read, so anything they say beyond the lane prompt is unverified here. If they conflict with this handback, the spec wins and this branch should be re-cut.
2. **The branch name.** The prompt names `claude/enso-indices-d091493`. The session harness pins pushes to `claude/enso-indices-d091493-0bpkf5`, so the work is there.
3. **`timeseries_values` has an `ingested_ts` column.** The prompt lists the source as `(ts, dataset, series, value, meta)`, but the table also has `ingested_ts timestamptz`, and that is the only honest source for `last_ingested`. The route uses `max(ingested_ts)`; `meta` is not read.
4. **"Canonical values JSON" was underspecified.** I chose `{index: values}`, sorted keys and compact separators, with the index set included and `last_ingested` excluded:
   * A request for oni only, roni only, or both gets a different tag each.
   * A re-ingest of identical numbers still returns 304, even though `last_ingested` in the body has moved. A client holding a 304 therefore keeps a stale `last_ingested`. This is the cost of a content-addressed tag, and I accepted it.
5. **Decimal handling now differs between two sibling routes.** `enso_catalog` casts `::float8` in SQL and *refuses* a Decimal. This route *converts* a Decimal in Python, as the prompt asked. Both fail loud on a leak (JSONResponse), but the two routes now differ.
6. **"A real response, read-only from production" is not a live HTTP response.** The branch isn't deployed, and this container cannot reach Neon's Postgres port (the TCP connect to `:5432` times out). The receipt is the production rows, proven byte-exact against Postgres's own sha256, served through the real route code. When this route deploys, a live `curl` should produce the same bytes and `W/"5e1fe1eb773bf3d4"` as long as the bank hasn't changed.
7. **I6 cannot assert "www, apex" without injecting them.** Those origins exist only in Railway's `ALLOWED_ORIGINS` env var; the code default is localhost. The test proves the route sits behind the real middleware with those origins loaded. I did not check that Railway's variable actually contains both.
8. **Zero rows → 503 blocks the whole request.** If `?index=oni,roni` is requested and roni is empty, the response is 503 and oni is not served either. `?index=oni` still works. I read "a requested index" as meaning exactly this.
9. **`first` / `last` were ambiguous.** I defined them as the first and last *served* months, so a trailing null does not become `last`. Production has no nulls today, so this only shows up in tests.
10. **Precision of `last_ingested`.** It is serialised with microseconds (`…:09.322000Z`), matching `datetime.isoformat`. If the spec wanted milliseconds, only the format changes.
11. **The date in the receipt name.** It is `2026_09_27`, as asked, which is the date of the ingest. The pull itself ran on 2026-09-28 UTC.
