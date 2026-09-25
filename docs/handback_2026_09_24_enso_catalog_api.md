# Handback — d091476 — `GET /api/enso/catalog`

**Date:** 2026-09-24. **Repo:** `energylake-api`. **Branch:** `claude/enso-catalog-api-d091476` (no suffix), cut from `origin/main` @ `e68b531`. Branch only: no PR, no merge.
**Spec:** pantry `docs/cc_spec_2026_09_24_enso_catalog_api.md`. It was read in full from pantry branch `arch/specs-2026-09-24-evening` @ `9a0481c`, because **the file is not on pantry `main`** (see §5).

---

## 1. The route

`GET /api/enso/catalog?classifier=cpc_oni|roni` (default `cpc_oni`).

| case | status | body | headers |
| --- | --- | --- | --- |
| banked, no/stale `If-None-Match` | 200 | `{classifier, catalog_version, computed_at, source, developing, counts, episodes, year_bins}`, in that order | `Cache-Control: max-age=3600`, `ETag: W/"<catalog_version>"` |
| `If-None-Match` matches (weak compare, comma lists, `*`) | 304 | empty | the same two |
| unknown classifier | 400 | `unknown classifier 'nino9'; allowed: cpc_oni, roni` | none |
| no run for the classifier | 404 | `{"detail": "no catalog banked for <classifier>"}` | none |
| pool / query raises | 503 | `db unavailable: …` | none |
| episodes/bins read short of the run's own `n_episodes`/`n_year_bins` | 503 | `catalog <v> read short (…) — a bank landed mid-read; retry` | none, not memoised (§5.3) |

Three queries on one `_pool.connection()` (so the pre-ping checkout hook still runs), one cursor. Queries 2 and 3 are scoped to the `catalog_version` from query 1. Memo: `_enso_catalog_cache: dict[classifier] -> (monotonic, payload, catalog_version)`, 60 s TTL, the same shape as `_delta_board_cache`. A memo hit still sets both headers and still answers 304. Gzip comes from the app-wide middleware. No new dependency.

## 2. The files

| file | change |
| --- | --- |
| `enso_catalog.py` (new, repo root) | `CLASSIFIERS`, `SEASONS` (the pantry's order, DJF = 0), `RUN_SQL` / `EPISODES_SQL` / `YEAR_BINS_SQL` (the spec's SQL verbatim), `build_payload(run_row, episode_rows, bin_rows)` (pure), `etag()`, `etag_matches()` |
| `main.py` | +80 lines: the route after `/api/weather/point/ladder` (the file's last route) and one line in the module docstring's route list |
| `README.md` | an "ENSO catalog" entry in Endpoints, just before the Structures room |
| `tests/test_enso_catalog.py` (new) | 15 tests, no DB |

`build_payload` does the numeric and season handling:
- A `Decimal` in `peak_oni`, `peak_window_oni` or `n3_minus_n4` raises `TypeError` naming the missing `::float8` cast. It is refused, not coerced.
- Season strings pass through unchanged but must be one of the twelve.
- Episodes are re-sorted by `(start_year, season index)` (§5.2).
- `enso_years` is coerced to a list of `int`.
- `computed_at` is converted to UTC with a `Z` suffix. Microseconds are kept if the bank wrote any.
- `counts` is the length of each list.

The route returns a `JSONResponse` (plain `json.dumps`), not a dict. FastAPI's `jsonable_encoder` would silently turn a `Decimal` into a `float`, and then a dropped cast could never go red.

## 3. Tests — T1–T7 (`pytest tests/test_enso_catalog.py`: 15 passed; whole suite 1750 passed = 1735 baseline + 15)

The route tests run against `_BankPool`, an in-memory stand-in for the three tables. It reads the SQL it is given the way Postgres + psycopg would:
- a `numeric` comes back as `Decimal` unless the SELECT says `col::float8`;
- `catalog_version = %(v)s` filters only if it is in the WHERE clause;
- `ORDER BY` sorts the season text alphabetically.

It also counts checkouts. This is what lets DB-free tests see R1 and R2 (§5.4).

| T | tests | asserts |
| --- | --- | --- |
| T1 | `test_t1_build_payload_key_order_types_and_nulls`, `…computed_at_normalised_to_utc_z`, `…build_payload_refuses_decimal_by_name`, `…episodes_sorted_chronologically_not_by_season_text`, `…route_serves_floats_through_the_real_sql`, `…only_the_current_version_is_served`, `…short_read_is_503_and_not_memoised` | top-level and row key order exact; `peak_oni` is `float`; `enso_years` is a list of `int`; `flavor`/`n3_minus_n4`/`notes` null serialise as `null`, never `""`; `computed_at` ends in `Z` (also from a −04:00 input); a `Decimal` is refused by name; MAM sorts before JAS in the same year; end to end the JSON carries floats; **two versions seeded, only the current run's rows served** (the R2 test §4 asks to add to T1); a short read → 503, not memoised |
| T2 | `test_t2_unknown_classifier_400_names_the_two` | 400, detail names both classifiers, no pool checkout, no cache headers |
| T3 | `test_t3_no_run_banked_404` | 404 `{"detail": "no catalog banked for roni"}`, no cache headers |
| T4 | `test_t4_db_unavailable_503` | 503 `db unavailable…`, no cache headers |
| T5 | `test_t5_cache_control_etag_and_304`, `…304_on_fresh_read_and_listed_or_strong_tags` | 200 has `max-age=3600` and `W/"<version>"`; replaying the ETag → 304, empty body, both headers; a listed/strong tag also matches; a non-matching tag → 200 |
| T6 | `test_t6_memo_one_checkout_per_classifier`, `…memo_expires_after_60s` | two `cpc_oni` requests → 1 checkout, and the memo hit carries both headers; `roni` next → 2nd checkout and **roni's** payload (47/78 shape: here 2/2); an entry older than 60 s is re-read |
| T7 | `test_t7_route_placement_and_docs` + the rest of the suite | the route is registered once, after `/api/weather/point/ladder`; the README entry and docstring line are present. `tests/test_cors.py` and `tests/test_sky_glm_mount.py` are unchanged (`git diff origin/main -- tests/` touches only the new file) and green in the 1750 |

## 4. Reds — one edit each, whole suite, sha-256 restore

Driven by a script (edit → `pytest -q` whole suite → restore original bytes → re-hash). Every restore matched. Nothing outside `test_enso_catalog.py` went red on any of them.

| red | edit | suite | went red | restore sha-256 |
| --- | --- | --- | --- | --- |
| R1 | `enso_catalog.py`: `peak_oni::float8 AS peak_oni` → `peak_oni` | 7 failed / 1743 passed | `test_t1_route_serves_floats_through_the_real_sql` (Decimal refused by `build_payload` → 500), plus every route test that reads `cpc_oni` | `0a0d6845…ae32c06b` = `0a0d6845…ae32c06b` OK |
| R2 | `enso_catalog.py`: drop ` AND catalog_version = %(v)s` from queries 2 and 3 | 6 failed / 1744 passed | `test_t1_only_the_current_version_is_served` (the old version's rows arrive; the count guard makes it 503), plus the other `cpc_oni` route reads | `0a0d6845…ae32c06b` OK |
| R3 | `main.py`: remove `"ETag": tag` from the headers dict | 2 failed / 1748 passed | `test_t5_cache_control_etag_and_304`, `test_t6_memo_one_checkout_per_classifier` | `d9462cde…57dcd17da` = `d9462cde…57dcd17da` OK |
| R4 | `main.py`: memo `get`/set keyed `"catalog"` instead of `classifier` | 2 failed / 1748 passed | `test_t6_memo_one_checkout_per_classifier` (roni after cpc_oni gets cpc_oni's payload), `test_t6_memo_expires_after_60s` | `d9462cde…57dcd17da` OK |

Full hashes: `enso_catalog.py` `0a0d68458546b662956a5377b49812700997cafd5d42caeb4c921168ae32c06b`; `main.py` `d9462cde7f20799182f97ab068584b64053be9e13447693e3eb832f57dcd17da`.

## 5. What the spec got wrong

1. **It is not on pantry `main`.** `docs/cc_spec_2026_09_24_enso_catalog_api.md` exists only on `arch/specs-2026-09-24-evening` (`9a0481c`). The lane read it from there.
2. **`ORDER BY start_year, start_season` is not chronological.** Seasons are text and sort alphabetically (`AMJ < ASO < DJF < … < MAM < … < SON`). Two episodes starting in the same year would come out in the wrong order. The SQL is kept verbatim, and `build_payload` re-sorts by `(start_year, SEASONS.index)` using the pantry's own order (enso/catalog.py, DJF = 0). This is tested.
3. **"One transaction … a bank landing between the queries cannot mix two versions" is only half true.** The pool's validate-on-checkout pre-ping (`SELECT 1`) runs on the non-autocommit connection before the route gets it. So the route's three statements join an already-open **READ COMMITTED** transaction:
   - Each statement gets its own snapshot.
   - `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ` is no longer possible.
   - The bank replaces every row in one transaction, so a bank landing between q1 and q2/q3 deletes the version q2/q3 ask for.

   The version scope stops a *mix*, but the result is a *short* read: empty lists under the old ETag, cached for an hour. The route compares what it read with the run's own `n_episodes`/`n_year_bins` and answers 503 (not memoised, no ETag) on a mismatch. This is a few lines beyond the spec, and it makes the §6 acceptance "counts equal `n_episodes/n_year_bins`" true by construction.
4. **§3 T1 as written cannot see R1.** `build_payload` on fixture rows never runs SQL, so dropping `::float8` can't turn it red. And if `build_payload` coerced `Decimal`→`float` (which "the numeric handling is here" invites), the cast would be redundant and R1 could never go red anywhere. Two fixes together:
   - `build_payload` refuses `Decimal` by name.
   - The route tests use a fake that returns `Decimal` for any uncast numeric.

   Likewise, "Decimal reaches the JSON encoder" only fails if the route bypasses FastAPI's `jsonable_encoder` (which converts `Decimal` silently), hence `JSONResponse`.
5. **§0's schema notes vs migration 240:**
   - `peak_oni`, `peak_year` and `peak_season` are `NOT NULL`, so "`peak_*` on an open episode" is never null. Only `end_year`/`end_season` are null while `open`.
   - `enso_catalog_runs.classifier` is the **PRIMARY KEY**, so there is exactly one run per classifier. `ORDER BY computed_at DESC LIMIT 1` is harmless but never chooses. The "two versions" state R2 tests can't exist in the bank today (`enso_episodes`' key is `(classifier, episode_id)` too). The test pins the scope anyway, and it matters again only if runs ever become history.
6. **"`README.md`'s route table gains one row" — README has no route table.** The only route table-like list is `main.py`'s module docstring. That got the row, and README got a short entry in its Endpoints section.
7. **ETag keyed on `catalog_version` alone.**
   - Both classifiers share `62f522e5…` (§0), so `cpc_oni` and `roni` carry the same ETag. That is legal, since validators are per-URL.
   - A **route code change** that alters the body without a new bank keeps the old ETag. A browser holding it gets 304 and keeps the old shape for up to an hour past a deploy.
   - If that matters, fold a payload schema tag into the ETag (e.g. `W/"<version>.1"`). I didn't do it because the spec fixes the ETag's form.
8. **Browser JS can't read the ETag cross-origin.** It is not a CORS-safelisted response header and the app's `CORSMiddleware` exposes no headers. The Explorer doesn't need it: the browser HTTP cache sends `If-None-Match` and resolves the 304 itself. But Explorer code that tries to read `ETag` itself will see `null`. `catalog_version` in the body is the thing to print.

## 6. STOP-D — reported

`NEON_DATABASE_URL` is **unset** in this lane (there is no `.env`), so the lane could not confirm that `enso_catalog_runs` / `enso_episodes` / `enso_year_bins` exist, and could not run the three queries live. Not exercised live:
- the real SQL against Postgres: the casts, `int[]` → list, `jsonb` → dict, `timestamptz` tz;
- real counts (cpc_oni 45/78, roni 47/78);
- the real `catalog_version`;
- gzip on the real body size.

The tests are DB-free by design. Production Neon was deliberately left alone: §6 gives it to the architect.

Architect's §6 run after the captain fires the Railway deploy:

```bash
curl -sI  https://<api>/api/enso/catalog                                  # 200, Cache-Control: max-age=3600, ETag: W/"62f522e5…"
curl -s   https://<api>/api/enso/catalog | jq .counts                      # {"episodes":45,"year_bins":78}
curl -s   'https://<api>/api/enso/catalog?classifier=roni' | jq .counts    # {"episodes":47,"year_bins":78}
curl -s -o /dev/null -w '%{http_code}\n' 'https://<api>/api/enso/catalog?classifier=nino9'   # 400
curl -s -o /dev/null -w '%{http_code}\n' -H 'If-None-Match: W/"<version from the first curl>"' https://<api>/api/enso/catalog  # 304
```

```sql
SELECT classifier, n_episodes, n_year_bins, catalog_version FROM enso_catalog_runs;  -- must equal .counts / ETag
```

Also worth eyeballing on the live body: episode order within any year that has two starts (§5.2), and that `peak_oni` is a bare number (no quotes).
