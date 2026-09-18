# Handback — 2026-09-18 — d091448m: mounting the GLM lightning proxy

**Repo:** `energylake-api` · **Branch:** `claude/glm-mount-d091448m-k3v9qp` (branch only, no PR)
**Spec:** `energylake-pantry` `docs/cc_spec_2026_09_18_sky_lightning.md` §d091448a · **Charter:** `arc_charter_2026_09_18_sky.md` (S-5)
**Upstream:** `energylake-pantry` `docs/receipts/sky-glm/handback_2026_09_18.md` + `price_2026_09_18.md` (lane d091448a, @ `9002595`)
**Suite:** `pytest` — **1724 passed**, 0 failed, 0 skipped (main was 1681; this adds 43).

The route the pantry lane shipped is mounted, `?bbox=` is added, and the
`ACAO: *` inversion that lane handed back is fixed rather than worked around.

**One thing was found that neither lane could have seen before this mount
existed, and it killed the process: `netCDF4` is not thread-safe, and this
mount runs one reader thread per satellite.** §3 below. It is fixed, pinned
by a test that fails as an assertion rather than as a crash, and it is the
reason this handback is longer than "three lines and a requirement".

---

## 1. WHAT SHIPPED

| file | what |
|---|---|
| `sky/` (4 files) | **vendored** from the pantry @ `9002595`, plus `?bbox=` and the netCDF4 lock. |
| `main.py` | `SkyExemptCORSMiddleware`; the readers dict; lifespan start/stop; `include_router`. |
| `requirements.txt` | `netCDF4>=1.6.0`, and `numpy>=1.24`. |
| `tests/test_sky_glm_mount.py` | 43 checks in seven sections. |
| `tests/fixtures/sky_glm/` | the pantry's real, unmodified 259,006 B GOES-19 object + `FIXTURE.json` (sha256 verified on copy). |

### Vendored, not imported — because there was no other option

Gate 0's finding was re-checked here rather than taken on faith. **This repo
has no code dependency on `energylake-pantry` at all**: no git dependency in
`requirements.txt`, no submodule, no `.gitmodules`, no path import, no
`sys.path` manipulation. The two repos share a **Neon database**, not a
package. So "imported" was never available and `sky/` is vendored.

The provenance and the exact delta are written into `sky/__init__.py` so the
next person to re-vendor diffs instead of guessing. The delta is two things:
`?bbox=`, and the lock in §3.

---

## 2. THE `ACAO: *` INVERSION — FIXED, AND ALL THREE ROWS MEASURED

The pantry lane handed this back as open item #2, owned by this repo. It is
closed.

**The finding.** `CORSMiddleware` is mounted app-wide with
`allow_credentials=True`. Starlette's `CORSMiddleware.send` calls
`allow_explicit_origin` for any origin the allowlist or the Vercel preview
regex admits, which **overwrites** the `Access-Control-Allow-Origin` the
route set. With credentials on it is specified never to emit `*`. So the
route answered `*` to everyone **except the origins we actually trust** —
and the dashboard works in all three rows, which is why this would have been
invisible in production forever.

**The decision, and the two alternatives rejected.** `/sky/*` is exempted
from the middleware entirely, via `SkyExemptCORSMiddleware`.

- *Drop `allow_credentials` app-wide* — fixes `/sky/*` and silently re-rules
  the CORS contract of all 73 existing `/api/*` routes. Rejected: a lightning
  layer does not get to do that.
- *A second CORSMiddleware scoped to `/sky/*`* — cannot work. Starlette's
  user middleware runs **before** routing, so both instances see every
  request and the app-wide one still overwrites.
- *Set the header in a `route_class` or response hook* — does not help. The
  middleware runs outside the router and overwrites whatever the route set,
  which is the defect itself.

A preflight on an exempt path is answered by the middleware itself with `*`,
because with the middleware bypassed there is no `OPTIONS` route on `/sky/*`
and FastAPI would answer 405 — which a browser reads as "CORS forbidden". A
plain dashboard `GET` never preflights, but one custom header makes it the
only path.

**Measured on the wire**, `uvicorn main:app`, `ALLOWED_ORIGINS` set to the
production list, reader warm:

| request | status | `ACAO` | `Vary` | `Allow-Credentials` |
|---|---|---|---|---|
| no `Origin` | 200 | **`*`** | *(none)* | *(none)* |
| `Origin: https://anyone.else` | 200 | **`*`** | *(none)* | *(none)* |
| **`Origin: https://energylake.io`** (allowlisted) | 200 | **`*`** | *(none)* | *(none)* |

and the same three rows hold on the 503 and 400 paths, which carry CORS too
— a 503 the browser cannot read is indistinguishable from a network failure,
and the dashboard's predicate caption needs the status code to name it.

**The control, same server, same run** — `/api/*` is untouched:

| request | `ACAO` | `Allow-Credentials` |
|---|---|---|
| `/api/tape/recent` preflight, `Origin: https://energylake.io` | `https://energylake.io` | `true` |

`tests/test_cors.py` passes unmodified. The exemption is prefix-scoped and a
test pins that `/skywalker` and `/api/sky/glm` are **not** exempt.

**Stated, not assumed:** `ACAO: *` and `Allow-Credentials: true` are
mutually exclusive under the CORS spec. Exempting `/sky/*` means a
cross-origin request to it carries no cookies. That costs nothing — every
route in this service is a public unauthenticated read and there is no cookie
to send.

**Rehearsed.** Swapping `SkyExemptCORSMiddleware` back for the plain
`CORSMiddleware` turns rows 1 and 2 green and **row 3 red**, reproducing the
pantry lane's measured inversion table exactly. The guard is not decorative.

---

## 3. THE FINDING THIS MOUNT CREATED — `netCDF4` IS NOT THREAD-SAFE

**Not a defect in the pantry's parser.** `tests/test_sky_glm.py` parses
single-threaded and is right to pass. It is a property of running **two
readers in one process**, which is exactly what mounting the route does, so
it could not have been found before this lane and it is fixed here.

**How it surfaced.** The first real boot of the mounted route. Both
satellites enabled, both threads parsing on the 20-s cadence. The uvicorn
process died with **SIGSEGV inside the first tick**, having logged, from the
`goes18` thread:

```
File "sky/glm.py", line 223, in read_flashes
  ds = netCDF4.Dataset("glm-inmemory", "r", memory=blob)
RuntimeError: NetCDF: Can't open HDF5 attribute
```

**Reduced to a deterministic reproduction** against one real 412,164 B
GOES-19 object — `netCDF4` 1.7.4, `numpy` 2.4.6, Python 3.11:

| | result |
|---|---|
| 80 parses, one at a time | **0 errors, exit 0** |
| 2 threads × 40 parses | **SIGSEGV (exit 139)** |

The wheel's bundled HDF5 is not built `--enable-threadsafe`, so concurrent
`Dataset` access corrupts library state.

**The fix.** `sky.glm._NETCDF4_LOCK`, a plain `threading.Lock` held across
the whole `Dataset` lifetime — open, every variable read, close — because
every one of those is a call into the same unsafe library state. After it:
4 threads × 50 parses, **200 ok, 0 errors**, every parse returning the same
737 flashes.

**The cost, stated.** Parse is 11.2 ms/file (pantry price receipt §2) and
both birds together publish 6 files/min, so this serialises **~67 ms of work
per minute**. Contention is nil, and the GIL already serialises the
surrounding Python. A parse subprocess or one process per satellite would buy
nothing here and cost a great deal.

**Second, smaller hole closed in the same place.** `read_flashes` promised
`GLMParseError` for anything unreadable, but caught only `OSError` around the
open — and netCDF4 raises its own library faults as **`RuntimeError`**. One
escaping would skip the reader's per-file handler, so the key would never
reach `_failed` and would be re-fetched **every 20 s forever** — the tight
retry loop the spec forbids, arrived at by way of an exception type rather
than a retry policy. `RuntimeError` is now caught at open and mid-read.

**Rehearsed.** Removing the lock turns
`test_read_flashes_actually_takes_the_lock` red **as an assertion**, which is
deliberate: the concurrency test alone would report a SIGSEGV that takes
pytest down with it — a true signal, but a miserable one to debug.

---

## 4. `?bbox=w,s,e,n` — AND THE ORDER IS THE WHOLE POINT

**It was not already plumbed.** `grep -rn bbox` over the pantry's `sky/` and
its 116-check suite returns nothing; the pantry handback §6 offered it as
"the route takes a bbox in one afternoon". So it is new here, written into
the pantry's arithmetic (`BBox`, `parse_bbox`, `filter_by_bbox`,
`format_bbox` in `sky/glm.py`) rather than bolted onto the handler.

**Filtered BEFORE thinning.** Reversing those two lines is the entire defect
the parameter exists to avoid: thinning to 5,000 across the hemisphere and
*then* cutting to the view hands a quiet West a nearly empty layer whenever
the Midwest is active, while the window plainly held Western flashes.
Filtering first spends the cap on the area asked for. A test builds exactly
that window — 5,000 energetic Midwest flashes plus 7 weak Western ones — and
asserts 7 come back; thin-first returns 0, rehearsed red.

**Measured on the wire**, `goes19`, `minutes=5`, one warm reader:

| request | body | features | `X-GLM-Thinned` |
|---|---|---|---|
| no bbox | 840,359 B | 5,000 | `5000/9912` — **4,912 discarded** |
| `bbox=-125,25,-67,50` (CONUS) | 490,256 B | 2,921 | `2921/2921` — **none discarded** |
| `bbox=-125,32,-114,42` (the West) | 2,174 B | 12 | `12/12` |

**Decisions, each stated rather than slipped in:**

1. **`w > e` wraps the antimeridian.** Real, not a flourish: GOES-18 is
   GOES-West and its field of view spans 180°, so a Pacific box is genuinely
   written `bbox=170,0,-150,30`. Reading that as inverted would silently drop
   every flash in it. `w == e` is **refused** — it is either a zero-width
   sliver or the whole globe and the route will not guess.
2. **A bad bbox is a 400 naming the fault, never a repaired box** — the same
   rule `minutes` already follows. A silently fixed box would be stamped onto
   `X-GLM-Bbox` as though it had been the one asked for.
3. **`X-GLM-Bbox`** is a sixth header, for the reason `X-GLM-Sat` is a fifth:
   `Cache-Control: public` plus a *second* varying parameter means a shared
   cache can hand a CONUS body to a Pacific request.
4. **`X-GLM-Thinned` counts after the bbox.** It reports the decision
   thinning actually made, so the honest caption for row 2 above is "2,921
   flashes", not "2,921 of 9,912". The window held more; the view did not,
   and the caption describes the view.
5. **`X-GLM-Newest` is taken after the bbox too.** Reporting the hemisphere's
   newest flash on a response that does not contain it is precisely the
   receipt-that-lies ruling S-3 exists to stop.

---

## 5. THE MOUNT, MEASURED FROM INSIDE THE APP

`uvicorn main:app`, both satellites, this box, 2026-09-18 ~21:15Z.

**Cold, before the first tick** — `/sky/glm/health` answers `ticks: 0`, and
`/sky/glm` is **503 with `ACAO: *` and `Cache-Control: no-store`**. That is
the designed degradation: the dashboard draws the layer *absent* with its
predicate caption rather than an empty layer that reads as fair weather over
a live storm. Startup never waits on the sky and never fails because of it.

**After the first tick — 13 s**, consistent with the pantry's 12.1 s:

| | goes19 | goes18 |
|---|---|---|
| ticks | 1 | 1 |
| flashes resident | **31,675** | 11,584 |
| files resident | 44 | 45 |
| newest-flash age at read | 28.9 s | 29.3 s |
| failed keys | **0** | **0** |

**`GET /sky/glm?sat=goes19&minutes=5`:**

```
HTTP/1.1 200 OK
access-control-allow-origin: *
cache-control: public, max-age=10
x-glm-window: 5m
x-glm-newest: 2026-09-18T21:14:59.382Z
x-glm-files: 14
x-glm-thinned: 5000/9922
x-glm-sat: goes19
x-glm-bbox: -
content-type: application/geo+json
```

**body: 840,390 B · 5,000 features.** The pantry priced this at 840,307 B;
the two agree to **83 bytes**, on a different day, a different hour and a
different flash population.

**Egress to NODD from this box: it works.** Listing `200 · 1,595 B · 418 ms`;
object `200 · 412,164 B · 258 ms`, and the object carries **no
`Access-Control-Allow-Origin`** — the recon confirmed a third time, and the
whole reason this proxy exists. Both readers completed their first tick with
**0 failed keys**, so the transport is exercised end-to-end, not just curled.

---

## 6. FOR THE ARCHITECT AND THE CAPTAIN

### Open item #4 — body size — is materially better than the price receipt could know

The price receipt's "2.5 MB/min down the wire to the browser" was measured
without an app. **This service already mounts `GZipMiddleware`**, and GeoJSON
compresses extremely well:

| | identity | gzip on the wire |
|---|---|---|
| `minutes=5`, no bbox | 840,357 B | **101,977 B (12.1%)** |
| `minutes=5`, CONUS bbox | 504,014 B | **59,682 B** |

So a 20-s poll is **~306 kB/min**, not 2.5 MB/min — and with a CONUS bbox,
~179 kB/min. The `Vary: Accept-Encoding` this adds does not vary on `Origin`,
so it does not fragment the cache by origin. **The ruling the captain owes is
now a much smaller one**, and the bbox is available either way.

### Still owed

| # | what | owner |
|---|---|---|
| 1 | **Egress from inside the *Railway service*** — still UNMEASURED. This lane measured the lane box and the local app; neither is Railway. `/sky/glm/health` on the deployed service closes it in one request, which is what that route is for. | architect / first deploy |
| 2 | **Parse cost on Railway's shared vCPU.** 11.2 ms/file is the lane box; 8.4 ms/parse wall under 4-thread contention here. At 6 files/min even a 5× miss is ~3.4 s/hour. | first deploy |
| 3 | **`SKY_GLM_ENABLED` / `SKY_GLM_SATELLITES`** are new env vars, default on / both birds. Cost if left on: ~2.4 MB/min egress and ~10 MB resident, continuously, whether or not anyone is looking at the map. **A ruling, not a defect** — if the captain wants the sky off until the dashboard lane ships, set `SKY_GLM_ENABLED=0` and the route answers a truthful 503. | captain |
| 4 | **`railway.json` says `NIXPACKS`; the service runs `RAILPACK`.** Observed by the pantry lane, observed again here, and **left** — out of scope for a route mount, but it is in this repo and somebody owns it. | captain |

### Departures from the pantry handback's "three lines and one requirement"

Each is here because leaving it out would have shipped something broken or
untrue, and each is argued above rather than slipped in:

1. **The netCDF4 lock** (§3) — without it the service segfaults on boot.
2. **`numpy>=1.24` as a second requirement line.** `sky/glm.py` imports numpy
   *directly*. It would arrive as a netCDF4 dependency anyway, but this file
   already carries the receipt for what happens when a direct import is left
   riding on someone else's: the `httpx` note above it, where "already a dep
   of the framework" was true in tests, false on Railway, and every
   `/api/weather/point*` read died as a bare 500.
3. **`SkyExemptCORSMiddleware`** (§2) — the handed-back finding; not three
   lines in any arrangement.
4. **Two env gates** (`SKY_GLM_ENABLED`, `SKY_GLM_SATELLITES`), following the
   existing `DD_WARM_ON_STARTUP` precedent, so the continuous cost in item 3
   is a setting rather than a redeploy.
5. **The fixture vendored** (259,006 B, sha256 checked against `FIXTURE.json`
   on copy) so the concurrency guard is pinned by a real file.
6. **An unknown `SKY_GLM_SATELLITES` entry is dropped with a warning, not
   raised.** `GLMReader` rejects an unknown satellite in its constructor —
   correctly, since a typo would otherwise list a prefix under the wrong
   bucket — but that constructor runs at **import time**, so letting it raise
   would turn one mistyped env var into a service that cannot boot at all and
   takes the other 73 routes down with it. A misconfigured sky costs the sky,
   not the service. Pinned by a test that imports in a subprocess.

## 7. SCOPE KEPT

- **No PR opened.** Branch pushed only.
- **No writes anywhere.** Ruling S-5 holds: the readers keep ≤15 minutes in
  memory and forget. No Neon statement, no R2, no disk. `/sky/*` is the only
  route family in this service that touches no database at all.
- **No existing route, test or behaviour changed.** 1681 tests passed on
  `main` before; the same 1681 pass now, plus 43.
- **`migrations/`, `railway.json`, `Procfile` untouched.** Nothing here is a
  dataset, a bank or a migration.
