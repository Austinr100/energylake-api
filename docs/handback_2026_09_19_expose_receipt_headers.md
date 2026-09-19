# Handback — 2026-09-19 — d091453: expose the receipt headers

**Repo:** `energylake-api` · **Branch:** `claude/expose-receipt-headers-d091453-siocp3` (branch only, no PR)
**Spec:** `energylake-pantry` `docs/cc_spec_2026_09_18_expose_receipt_headers.md` (@ `a2b8f57`)
**Upstream:** lane d091448b's chromium measurement (0 of 6 readable / 6 of 6 with the header)
**Suite:** `pytest` — **1735 passed**, 0 failed, 0 skipped (main was 1724; this adds 11).

`/sky/glm` has sent six `X-GLM-*` receipt headers since the mount lane. A
browser could read none of them. They are now exposed, by a list derived from
the same dict they are set in, and `Timing-Allow-Origin: *` rides with them on
`/sky/*` only.

**What this handback does NOT claim.** Every check below is a string on a
response in-process. A browser's CORS filter is not in this process and cannot
be. The acceptance test is d091448b's receipt flipping from `blocked` to
`exposed` with no dashboard change, after the captain merges and Railway
deploys, and it is the architect's measurement, not this lane's.

---

## 1. WHAT SHIPPED

| file | what |
|---|---|
| `sky/glm.py` | `RECEIPT_HEADER_PREFIX` + `build_expose_headers()`; `build_receipt_headers()` now returns `Access-Control-Expose-Headers` (derived) and `Timing-Allow-Origin: *`. |
| `sky/glm_route.py` | `_plain_headers()` gains `Timing-Allow-Origin: *`; the CORS note gains the half of the sentence this lane is about. |
| `sky/__init__.py` | exports the two new names; the "whole delta" note is corrected — it was no longer true. |
| `tests/test_sky_glm_mount.py` | §8, 11 checks. |

`main.py` is **untouched**, deliberately — see §2.

```
 sky/__init__.py             |  18 ++++-
 sky/glm.py                  |  56 +++++++++++++++-
 sky/glm_route.py            |  43 +++++++++++-
 tests/test_sky_glm_mount.py | 158 ++++++++++++++++++++++++++++++++++++++++++++
 4 files changed, 269 insertions(+), 6 deletions(-)
```

## 2. WHERE IT WENT, AND WHY NOT THE MIDDLEWARE

`SkyExemptCORSMiddleware` exists to keep the app-wide **credentialed**
`CORSMiddleware` off `/sky/*`. That is its whole job, and it is the reason
`glm_route.py` can say "the header contract therefore lives in exactly one
place — here." A response header set in the middleware would split the
contract in two and falsify that sentence in the file the next reader trusts.
So the middleware is untouched, and the new headers are set beside the ones
they describe: `build_receipt_headers()` for the 200s, `_plain_headers()` for
the error paths.

That is asserted, not just stated.
`test_the_expose_header_comes_from_the_route_not_the_middleware` mounts the
router in a bare `FastAPI()` with **no CORS middleware at all** and still gets
the full exposed list — and then reads `main.py` and asserts the middleware
class body contains neither header name.

## 3. HOW THE LIST IS DERIVED RATHER THAN DUPLICATED

`build_receipt_headers()` builds its dict, then reads the list off that dict:

```python
headers = { ... "X-GLM-Window": ..., "X-GLM-Sat": ..., "X-GLM-Bbox": ... }
headers["Access-Control-Expose-Headers"] = build_expose_headers(headers)
return headers
```

`build_expose_headers(headers)` joins every key of the mapping it is handed
that starts with `RECEIPT_HEADER_PREFIX` (`X-GLM-`). There is no second list
of six names anywhere in the repo. Adding a seventh receipt header to that
dict literal exposes it in the same keystroke; there is no other place to
remember.

**Rehearsed in both directions, not assumed:**

* *Red.* A seventh header set OUTSIDE the derivation (`headers["X-GLM-Seventh"]`
  added in `serve_glm` after the call) →
  `test_exposed_list_is_exactly_the_receipt_headers_actually_sent` **FAILS**:
  `Extra items in the right set: 'x-glm-seventh'`. That is the guard the spec
  asked for: sent-but-not-exposed fails.
* *Green.* The same seventh header added to the dict literal inside
  `build_receipt_headers` → that test **passes unchanged**, the header exposed
  with no second edit.

The literal census of "there are six today" lives in exactly one test
(`test_nothing_but_the_receipt_headers_is_exposed`), so a seventh header costs
one deliberate line from a human and nothing else.

Nothing but the `X-GLM-*` names is exposed: `Content-Type`, `Cache-Control`
and `Vary` are CORS-safelisted already, and listing them would make the
receipt look like it governs more than it does.

## 4. `Timing-Allow-Origin` — YES, on `/sky/*` only

**Added.** `Timing-Allow-Origin: *` on every `/sky/glm` response, the 200s and
the 400/503 error paths alike.

**Why yes.** Resource Timing reports 0 transferred bytes for this route today,
so the page cannot weigh its own traffic and d091448b's budget line is a
projection by necessity. The reason it is safe is specific to this route and
does not generalise: `/sky/glm` is unauthenticated, public-domain NOAA data,
already served `Access-Control-Allow-Origin: *`. The body is world-readable,
so its size and timing disclose nothing the body itself does not.

**Why also on the errors.** A page weighing its own traffic has to be able to
weigh the failures; an error body carries strictly less than the world-readable
one that made the header safe here.

**Where it is NOT.** `/api/*` is credentialed and gets neither header —
asserted by `test_api_routes_get_neither_header`. `/sky/glm/health` is
unchanged (it sets no custom headers at all today, and widening that was not
this lane's to do).

No `Access-Control-Expose-Headers` on the error paths: those responses set no
`X-GLM-*` header, so the derived value would be the empty string. The status
code is the whole receipt there, and the status code was never hidden.

## 5. THE THREE CORS ROWS, RE-MEASURED

Through the real `main.app`, `GET /sky/glm?sat=goes19&minutes=5`, one stub
reader, all four headers read off the response:

| row | ACAO | ACAC | Vary | Access-Control-Expose-Headers | TAO |
|---|---|---|---|---|---|
| no `Origin` | `*` | absent | absent | all six | `*` |
| `Origin: https://anyone.else` | `*` | absent | absent | all six | `*` |
| `Origin:` allowlisted (Vercel preview regex) | `*` | absent | absent | all six | `*` |
| `Origin: https://energylake.io` (prod allowlist app) | `*` | absent | absent | all six | `*` |

All six = `X-GLM-Window, X-GLM-Newest, X-GLM-Files, X-GLM-Thinned, X-GLM-Sat, X-GLM-Bbox`.

The error paths hold too: 503 and 400 → `ACAO: *`, `TAO: *`, for every origin.

**The control is unchanged.** `/api/tape/recent` preflight with the allowlisted
origin still answers `access-control-allow-origin: <the origin, echoed>` and
`access-control-allow-credentials: true`, with **no** `Timing-Allow-Origin` and
**no** `X-GLM-*` in any expose header. The path-lookalike test
(`/skywalker`, `/api/sky/glm`, websocket scope) still passes untouched.

## 6. §4 OF THE SPEC — THE PANTRY DRIFT, MEASURED AND NOT FIXED

Measured 2026-09-19, `energylake-pantry` @ `a2b8f57` (main) vs
`energylake-api` @ `e53fd7b` (main, i.e. **before** this lane's diff). Blob
hashes reproduce the spec's table exactly:

| file | pantry | energylake-api | |
|---|---|---|---|
| `glm_reader.py` | `d00dbff` 17,290 B | `d00dbff` 17,290 B | identical |
| `glm_route.py` | `b5766a4` 9,798 B | `9acdc07` 12,270 B | differ |
| `glm.py` | `8f30833` 26,038 B | `dfcf3cc` 35,328 B | differ |
| `__init__.py` | `062962b` 1,235 B | `8164068` 2,568 B | differ |

**Direction: the API copy is strictly ahead. Nothing exists in the pantry
copy that is absent here** — across all three differing files, zero top-level
symbols are pantry-only.

### `glm.py` — 32 shared top-level symbols, 30 byte-identical

| symbol | state |
|---|---|
| `_NETCDF4_LOCK`, `_read_flashes_locked` | **API only** — the netCDF4 lock (d091448m) |
| `BBox`, `BBox.contains`, `BBox.crosses_antimeridian`, `parse_bbox`, `filter_by_bbox`, `format_bbox`, `_trim` | **API only** — `?bbox=` (d091448m) |
| `read_flashes` | **differs** — two changes, both listed below |
| `build_receipt_headers` | **differs** — `bbox=` parameter + `"X-GLM-Bbox"` key + its docstring |
| the other 30 (`Flash`, `GLMKey`, `FlashWindow` and its methods, `parse_glm_key`, `hour_prefix`, `thin_by_energy`, `flashes_to_geojson`, `geojson_bytes`, `_col`, `_sig`, `_as_utc`, `_iso_z`, `GLM_SATELLITES`, `THIN_CAP`, `WINDOW_MAX_MINUTES`, …) | **byte-identical** |

The module docstring is byte-identical too.

`read_flashes` carries exactly two deltas and they are separable:
1. **the lock** — the body moved verbatim into `_read_flashes_locked`, called
   under `with _NETCDF4_LOCK:`. Diffed line by line: the moved body is the
   pantry's body with no other change.
2. **`GLMParseError` coverage** — `except OSError` widened to
   `except (OSError, RuntimeError)` on open, and a new `except RuntimeError`
   around the variable reads. netCDF4 raises its own library faults as
   `RuntimeError`; unconverted, they escape the reader's per-file handler, the
   key never reaches `_failed`, and it is re-fetched every 20 s forever.

### `glm_route.py` — 8 shared symbols, 5 identical

`parse_query`, `serve_glm`, `build_router` differ. All three are `?bbox=`
plumbing — plus one thing the captain should see named rather than inferred:

* **the lock scope in `serve_glm` narrowed.** The pantry computes `newest`
  inside `with reader.lock:`; the API copy holds the lock only for
  `window.select` / `files_in_window`, then does `filter_by_bbox` and `newest`
  outside it. `Flash` is an immutable NamedTuple and `select` returns a new
  list, so the snapshot is safe — but it *is* a behavioural difference beyond
  "a bbox parameter", and it is the only one.
* `build_router`'s docstring lost the pantry's "how to mount this in
  energylake-api" recipe (now true of this repo) — prose only.
* `_plain_headers`, `_err`, `BadRequest`, `DEFAULT_SAT`, `DEFAULT_MINUTES`:
  identical (before this lane's `Timing-Allow-Origin`).
* The module docstring differs: the vendoring header and the `ACAO: *` note.

### `__init__.py`

`__all__` differs by the five bbox names — `BBox`, `parse_bbox`,
`filter_by_bbox`, `format_bbox` — **and `geojson_bytes`**. That last one is
the one item of drift outside the bbox / lock / `GLMParseError` story:
`geojson_bytes` is defined **identically in both** `glm.py` copies, but only
the API's package re-exports it. Harmless, and a genuine divergence in the
package surface.

### So, for the ruling

Beyond `?bbox=`, the netCDF4 lock and the `GLMParseError`/`RuntimeError`
widening, exactly two things have drifted: the narrowed lock scope in
`serve_glm`, and `geojson_bytes` missing from the pantry's `__all__`.
Everything else that differs is prose. **The safety-relevant asymmetry is
unchanged and unaddressed by this lane:** the pantry's `sky/glm.py` has no
`_NETCDF4_LOCK`, so anything that mounts the pantry copy in a threaded process
inherits the SIGSEGV that lock exists to prevent (80 sequential parses clean;
2 threads × 40 → exit 139).

Not back-ported, not retired, not touched. Which copy is canonical is a
captain-class call, and this lane's job was to hand it the facts.

## 7. WHAT IS STILL OPEN

* **The acceptance test.** The receipt flip is taken in a browser after
  deploy, by the architect. A green suite does not claim it.
* **The pantry copy** (§6) — awaiting a ruling.
* **The readers' silence** — no tick, parse or error line; `/sky/glm/health`
  is their only instrument. Real, booked separately, untouched here.
* **`/sky/glm/health`** sets no CORS headers of its own at all. Out of scope
  and noted only so it is not mistaken for something this lane handled.
