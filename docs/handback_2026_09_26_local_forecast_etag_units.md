**STOP-N: NWS could not be reached from the build box.** `curl https://api.weather.gov/points/33.942,-118.409` got `CONNECT tunnel failed, response 403` from the session's egress proxy on 2026-09-26. So everything here was built and tested on fixtures, and the `units=us` twins are derived by script, not recorded.

# Handback — d091488 — `/api/local/forecast`: `pm180`, content ETag, `units=us`

**Spec:** pantry `docs/cc_spec_2026_09_26_local_forecast_etag_units.md` (pantry main `b2dbf1a`).
**Repo / branch:** `energylake-api`, `claude/local-forecast-etag-units-d091488`, cut from `origin/main` `d544c28` (#79). Branch only: no PR, no merge, no Railway change.
**Rulings built:** D-09-25-17 (§2.0, built first), D-09-25-15, D-09-25-16.
**Suite:** 1874 passed at the base. **1899 passed** on the branch (25 new tests, 3 pins amended, 0 skipped).

## What changed

| § | file | change |
| --- | --- | --- |
| 2.0 | `weather_point.py` | `SidecarHeader` accepts `lon_convention == "pm180"`, but only when `nx × \|dlon\| == 360` within 1e-6. A `pm180` header on a partial window is a 502 `{"error": "pm180 lon_convention on a partial window", key, lon_convention, shape, dlon, lon_span}`. The unknown-convention 502 body is unchanged. `locate` and `normalize_lon` are not touched: `pm180` only reaches `locate`'s existing full-circle branch. |
| 2.0 | `tests/fixtures/weather_sidecar_header_global_2026092518_t2m_f000.json` | Copied byte-for-byte from pantry `docs/receipts/local-forecast-etag-units-d091488/fixtures/t2m_global_20260925T18Z_f000_header.json`: 660 bytes, sha-256 `9ff46b1a592f42a0cc01d445637a4562a791ad6e07d164e10967f6116c982818`, no trailing newline. The test pins the digest. |
| 2.0 | `tests/test_local_forecast.py` | `GLOBAL_HEADER` is now `{k: PROD_HEADER[k] for k in GEOMETRY_KEYS + ("model", "crop")}`. `FakeGlobalBank` serves `{**PROD_HEADER, "param": …, "units": …}`. The hand-typed `"west_negative_monotonic"` and `"endianness"` are gone; `byte_order` comes from the bytes. |
| 2.1 | `local_forecast.py` | `content_etag(payload)` returns `W/"<arm>:<sha256 hex[:32]>"` over `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` of the payload, after removing `receipts.generated_at` and `receipts.memo` (`ETAG_EXCLUDED_RECEIPTS`). It is pure. It raises `ValueError` if `receipts` has no `arm`. `etag()` is deleted. |
| 2.1 | `main.py` | The route calls `_lf.content_etag(payload)`. `Cache-Control` is unchanged. `_enso.etag_matches` is still used for the comparison. |
| 2.2 | `nws_arm.py` | `forecast` and `forecastHourly` are requested with `{"units": "us"}`. The module docstring now cites D-09-25-16. **No conversion code changed.** |
| 2.3 | `scripts/build_nws_us_fixtures.py` | Derives `forecast.us.json` and `forecastHourly.us.json` from the hand-built originals. °C becomes integer °F (round half away from zero) with `temperatureUnit "F"`. km/h wind strings become integer mph. `properties.units` becomes `"us"`. The provenance keeps "hand-built". `--check` exits 1 if a twin is stale; T20 runs it. |
| — | `tests/test_local_forecast.py` `FakeNws` | Serves the `.us.json` twin when a forecast read carries `units=us`, as NWS would. The SI originals are still served for `units=si`, or through `override` for tests that pin SI conversion. |
| — | `README.md` | The local-forecast row now names the content ETag (D-09-25-15) and `units=us` (D-09-25-16). |

**§2.2 audit (SI assumptions in conversion code): none found.** `_temp` reads `temperatureUnit` or the QV `unitCode` through `to_celsius`, which already handles `F`. `_period_wind` → `parse_wind_speed` → `to_ms` reads the trailing unit token, which already handles `mph`. Observations keep their `unitCode`s. Nothing was hard-coded to `degC` or `km_h-1`, so nothing needed fixing.

**STOP-E: not triggered.**
- `_enso.etag_matches` strips `W/` and splits `If-None-Match` on commas. The new tag is `arm:` plus hex, with no comma or quote, so the shared comparison works unchanged.
- The ENSO route and `enso_catalog.py` are untouched.
- No other consumer in the repo parses the old tag grammar.

## §3 — Tests

| T | test(s) | result |
| --- | --- | --- |
| T22 | `test_T22_production_header_is_the_pantrys_bytes_and_reads_as_pm180`, `test_T22_locate_on_the_production_header_matches_the_independent_cell[Vancouver, Fiji, Samoa, seam 179.9E, seam 179.9W]` | pass. 660 bytes, sha `9ff46b1a…`, `lon_convention "pm180"`, `inferred == []`, full-circle; each point's `(j, i)` equals `_pm180_cell` |
| T23 | `test_T23_global_header_agrees_with_the_production_header` | pass. Checks `shape, lat0, lon0, dlat, dlon, lat_order, lon_convention, dtype, byte_order` |
| T24 | `test_T24_vancouver_answers_from_the_model_arm_on_the_production_header`, `test_T24_lax_days_8_to_10_from_the_model_on_the_production_header` | pass. Vancouver: 200, `arm "model"`, `now.source "model · GFS 12Z f000"`, only `/global/` headers read. LAX: 10 daily rows with 8–10 `model · GFS 12Z …`, no "unavailable" note |
| T25 | `test_T25_pm180_on_a_partial_window_is_a_502_naming_the_shape`, `test_T25_an_unknown_convention_is_the_unchanged_502` | pass. The na3 geometry with `pm180` gives a 502 with `shape [222, 583]` and `lon_span 145.75`. An unknown string gives exactly the old body |
| T17 | `test_T17_request_only_fields_do_not_move_the_tag`, `test_T17_every_weather_field_moves_the_tag[now.valid, now.age_min, alerts[0], hourly[0].valid, daily[0].source]`, `test_T17_a_payload_without_an_arm_is_refused_and_the_old_tag_is_gone` | pass |
| T18 | `test_T18_a_new_observation_under_the_same_issuance_is_a_200`, `test_T18_a_new_alert_under_the_same_issuance_is_a_200` | pass. At t0 + 6 min with `If-None-Match: A`, the forecast is served from the memo with the same `issued_at`, and the response is 200 with the new `now.source` (`observed 21:10Z`) or the new alert, and a new tag |
| T19 | `test_T19_an_unchanged_body_is_an_empty_304_with_the_same_headers` | pass. The clock moves 20 s, so `generated_at` changes but `age_min` does not, and the memo flips miss→hit. The answer is 304 with an empty body and identical `ETag` and `Cache-Control` |
| T20 | `test_T20_forecast_urls_carry_units_us`, `test_T20_us_twin_round_trips_to_nws_own_integer_fahrenheit`, `test_T20_wind_in_mph_is_read_to_a_tenth_of_a_metre_per_second`, `test_T20_twins_are_current_with_their_si_sources` | pass. 80 °F → 26.7 → 80. All 48 served hourly rows have `t` = the twin's °F at 0.1 °C, and `cToF(t)` gives back the twin's integer. `"10 mph"` → 4.5 m/s, on the route too |
| T21 | the whole suite | **1899 passed**. Amendments are listed below |

### T21 — amended pins

| pin | was | now | why |
| --- | --- | --- | --- |
| #78 T8 `test_T8_cache_headers_and_etag_per_arm` | `'W/"nws:2026-09-25T18:31:04Z:KLAX"'`, `'W/"model:2026-09-25T12:00:00Z:2026-09-25T12Z"'` | `etag == lf.content_etag(body)` and matches `W/"(nws\|model):[0-9a-f]{32}"` | D-09-25-15 |
| #78 T7 `test_T7_every_nws_call_carries_units_si_on_both_forecasts` | `params == {"units": "si"}` | renamed `…_units_us_…`, `params == {"units": "us"}`, and asserts both calls were made | D-09-25-16 |
| #78 T2 `test_T2_si_units_a_known_fahrenheit_arrives_as_celsius` | fake served the SI hourly by default | serves `forecastHourly.json` via `override`. Assertions unchanged | the route now asks for `units=us`; this pin is conversion **from SI**, so the SI original is served explicitly (§2.3 "the SI files stay for the tests that pin conversion from SI") |

`test_T8_matching_if_none_match_is_an_empty_304` passes unchanged: two requests at a pinned clock give identical content. No #79 (d091485) pin asserted the old tag grammar, so none needed amending. T16's `GLOBAL_HEADER` shape assertion passes unchanged against the derived header.

## §4 — Reds

Each red was applied to commit `bdf55ef`, then `tests/test_local_forecast.py` was run and the tree reverted. The script is reproducible; each edit is exactly the one named.

| red | edit | must go red | went red |
| --- | --- | --- | --- |
| R15 | `"pm180"` removed from the accepted set | T22, T24 | **T22** (both), **T24** (both), plus T25-partial (it now gets the unknown-convention 502 first) and 48 others. Every model-arm test now reads the production header, so all of them 503. That wide blast is the point of D-09-25-17 |
| R16 | `GLOBAL_HEADER["lon_convention"]` typed back to `"west_negative_monotonic"` | T23 | **T23** (only) |
| R17 | the full-circle guard dropped | T25 | **T25-partial** (only) |
| R11 | `content_etag` also drops `now` | T17, T18 | **T17** (`now.valid`, `now.age_min`), **T18-observation** |
| R12 | the route reverts to `W/"arm:issued_at:station"` | T18 | **T18** (observation and alert), plus the amended T8 |
| R13 | `content_etag` keeps `generated_at` | T19 | **T19**, plus T17-request-only |
| R14 | the forecast URLs revert to `units=si` | T20 | **T20** (URLs, round-trip), plus the amended T7 |

Under R11, T18-alert stays green, because the alert is outside `now` and still moves the tag. That is correct: R11 hides the observation, not the alerts.

## §6 — Fence check

The diff touches: `weather_point.py` (the accepted set and the guard, 11 lines), `local_forecast.py`, `nws_arm.py` (two params and the docstring), `main.py` (the tag line), `README.md`, `tests/test_local_forecast.py`, `scripts/build_nws_us_fixtures.py`, and three new fixtures.

Not touched:
- `enso_catalog.py`, `model_arm.py`, `select_arm`, the outline, the icon table, CORS, `railway.json`, secrets;
- the locator (`locate`, `normalize_lon`, `is_full_circle`);
- `tests/test_weather_point.py` (its T10 sha pin still passes).

## What the spec got wrong

1. **§2.3 "`tests/fixtures/nws/` holds hand-built SI bodies"**: only half true. `forecastHourly.json` is SI (°C, km/h). `forecast.json` was already `properties.units "us"` with °F and mph; #78's README says so, because it proved conversion by the carried unit. So the script's `forecast.us.json` is a pass-through: identical to `forecast.json` except for `_provenance`. It is kept so that both forecast kinds have a twin from the same script, as §2.3 asks.
2. **Appendix A targets a `CLAUDE.md` this repo does not have.** `energylake-api` has no `CLAUDE.md`, and the ruling ledger and "Known traps" live in the pantry's. Writing to the pantry is outside this branch, so **Appendix A is not landed.** Its text, for the architect to place:
   > **D-09-25-17:** the API reads the pantry's `pm180` on a full-circle axis only, and a fake of a cross-repo object is derived from that object's committed bytes. **D-09-25-15:** the local forecast's ETag is the sha of its content minus `generated_at`/`memo`. **D-09-25-16:** NWS is read in `units=us`, and the payload stays SI.
   > Trap: *a tag that names one of a body's sources makes every other source's change a 304; an ETag must move when the bytes move.*

   The API `README.md` row now carries D-09-25-15 and D-09-25-16.
3. **T20's `cToF` is not in this repo.** The page's °C→°F lives in the site repo. T20 uses a local replica that rounds half away from zero, as NWS prints. JavaScript's `Math.round` rounds half toward +∞, which differs only on negative `.5` values. The twin has none, but a sub-freezing page could still disagree with weather.gov by one degree at an exact half. That check belongs in the site repo.
4. **T24 "production header" versus the fake's run.** The committed header is 18Z (`cycle 18`, `run_date 20260925`, `valid …T18:00`). The fake bank still serves it under `RUN = 12Z` keys, which is why T24's `now.source` is `GFS 12Z f000`. The reader ignores those header fields, so this is harmless, but "fed the production header" means the production geometry and conventions under the fake's run.
5. **The full-circle guard checks span only, not origin.** D-09-25-17 asks only that `nx × |dlon| == 360`, and that is what was built. A `pm180` header with `lon0 = 0` is accepted: #79's `test_T7_locator_matches_pantry_locate_cell_on_both_axis_origins` builds exactly that, and `locate`'s modular arithmetic reads it correctly. If `pm180` is meant to imply `lon0 == -180`, that is a further ruling.

## Acceptance (architect, production, after merge and deploy)

Unchanged from spec §7. Nothing here was checked against production. Railway was not touched, and NWS is unreachable from this box (STOP-N).
