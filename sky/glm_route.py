"""`GET /sky/glm?sat=&minutes=` — the handler, and the binding that mounts it.

────────────────────────────────────────────────────────────────────────────
VENDORED FROM `energylake-pantry` @ 9002595 BY LANE d091448m, 2026-09-18.

The pantry lane (d091448a) wrote and measured this; this repo is the service
that Gate 0 found actually deploys, so the package is vendored here rather
than imported. Two changes were made on the way in, both recorded in
`docs/handback_2026_09_18_sky_glm_mount.md`:

  * `?bbox=w,s,e,n`, filtered BEFORE thinning (see `serve_glm`). The price
    receipt handed this lane a number — 840,307 B per `minutes=5` poll — and
    named three levers; this is the one that shrinks the body without making
    the layer less true.
  * The `ACAO: *` finding below is FIXED, in `main.py`, not worked around.

Everything else is byte-for-byte the pantry's. When the pantry's copy moves,
diff before re-vendoring.

────────────────────────────────────────────────────────────────────────────
WHY THIS FILE WAS WRITTEN IN THE PANTRY AND THE ROUTE IS NOT THERE.

Gate 0 measured the deploy path and it is not this repository. Railway
service `926ba1e5-b946-4a13-bca3-9f87970353f4` ("web", project
`comfortable-love`) builds from **`Austinr100/energylake-api`** `main` with
`uvicorn main:app`. `energylake-pantry` has no web framework, no `Procfile`,
no `railway.json` and has never served an HTTP route — the same wall
`docs/recon_model_runs_route_2026_08_30.md` §3 hit, and it resolved it the
same way this file does: the contract, the arithmetic and the tests live
here; the service wraps them rather than re-deriving them, so there is one
implementation and not two that drift.

`serve_glm()` below is the whole handler and it is framework-free — it
takes strings and returns `(status, headers, body)`. `build_router()` is
eleven lines of FastAPI around it, and is only importable where FastAPI is
installed. The pantry's own dependency set is unchanged.

────────────────────────────────────────────────────────────────────────────
THE `ACAO: *` INVERSION — HANDED BACK BY d091448a, FIXED HERE BY d091448m.

The spec requires `Access-Control-Allow-Origin: *`. `energylake-api` mounts
`CORSMiddleware(allow_origins=ALLOWED_ORIGINS, allow_credentials=True, ...)`
app-wide. Starlette's middleware **overwrites** the response's
`Access-Control-Allow-Origin` for any request whose `Origin` is in the
allowlist or matches the Vercel preview regex, and with
`allow_credentials=True` it is specified to echo the origin — it is not
permitted to emit `*`. Mounting this router under that middleware unchanged
yields the inversion the pantry lane measured and handed back:

    Origin: https://energylake.io   ->  ACAO: https://energylake.io   (echoed)
    Origin: https://anyone.else     ->  ACAO: *                       (ours)
    no Origin at all                ->  ACAO: *                       (ours)

— `*` for everyone EXCEPT the origins we actually trust. The dashboard works
either way, which is why this would have been invisible in production.

**The fix is in `main.py`, not in this file:** `SkyExemptCORSMiddleware`
passes `/sky/*` through untouched (and answers its preflight itself with
`*`), so the headers this module sets are what the wire says in all three
rows. The header contract therefore lives in exactly one place — here — and
`tests/test_sky_glm_mount.py` asserts all three rows end-to-end through the
real app, so it cannot drift back silently.
"""

# NO `from __future__ import annotations` IN THIS FILE, AND IT IS NOT AN
# OVERSIGHT. Postponed annotations turn `request: Request` into the STRING
# `"Request"`, and FastAPI resolves that string against the MODULE globals.
# `Request` is imported inside `build_router` (so the pantry never needs
# FastAPI), so the name is not there — FastAPI then falls back to treating
# `request` as an ordinary query parameter and every call 422s with
# `{"loc":["query","request"],"msg":"Field required"}` before the handler
# runs. Measured on fastapi 0.115.6 / starlette 0.41.3, the versions
# `energylake-api/requirements.txt` pins. Native `X | None` syntax is used
# instead, which needs no future import on Python 3.10+.

from datetime import datetime, timezone
from typing import Mapping

from sky.glm import (
    GLM_SATELLITES,
    THIN_CAP,
    WINDOW_MAX_MINUTES,
    BBox,
    GLMParseError,
    build_receipt_headers,
    filter_by_bbox,
    flashes_to_geojson,
    geojson_bytes,
    parse_bbox,
    thin_by_energy,
)

DEFAULT_SAT = "goes19"
DEFAULT_MINUTES = 5


class BadRequest(ValueError):
    """A query this route refuses, with the message the client is told."""


def parse_query(
    sat: str | None,
    minutes: str | int | None,
    bbox: str | None = None,
) -> tuple[str, int, BBox | None]:
    """Validate `?sat=`, `?minutes=` and `?bbox=`. Refuses rather than clamps.

    Clamping `minutes=60` down to 15 would answer a question nobody asked
    and stamp `X-GLM-Window: 15m` on it — a receipt that contradicts the
    request that produced it. A 400 naming the bound is the honest answer,
    and `?bbox=` is held to the same rule: a malformed or inside-out box is
    a 400 naming the fault, never a silently repaired box that the receipt
    then stamps as if it had been asked for.
    """
    sat = (sat or DEFAULT_SAT).strip().lower()
    if sat not in GLM_SATELLITES:
        raise BadRequest(
            f"unknown sat {sat!r}; expected one of {sorted(GLM_SATELLITES)}")

    try:
        box = parse_bbox(bbox)
    except GLMParseError as exc:
        raise BadRequest(str(exc)) from None

    if minutes is None or minutes == "":
        return sat, DEFAULT_MINUTES, box
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        raise BadRequest(f"minutes must be an integer, got {minutes!r}") from None
    if not 1 <= m <= WINDOW_MAX_MINUTES:
        raise BadRequest(
            f"minutes must be 1..{WINDOW_MAX_MINUTES}, got {m} "
            "(the reader retains no more than that)")
    return sat, m, box


def serve_glm(
    readers: Mapping[str, "object"],
    sat: str | None = None,
    minutes: str | int | None = None,
    now: datetime | None = None,
    bbox: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """The handler. `(status, headers, body)`; no framework, no I/O.

    `readers` maps a satellite code to anything carrying `.window` and
    `.lock` — a `GLMReader` in production, a bare stub in the tests.

    `bbox` is `"w,s,e,n"` in degrees, or None. It is applied to the selected
    flashes BEFORE thinning; see the comment at the filter call below, which
    is where the value of the parameter actually lives.

    Statuses, and why each is the one it is:

      200  a window exists. **Including an empty one** — zero flashes over
           the Pacific at 04Z is the normal state of the sky, not an error,
           and the receipt headers say how many files were read so a reader
           can tell "quiet" from "broken".
      400  the query is out of contract (`parse_query` above).
      503  no reader for that satellite, or the reader has not completed a
           tick yet. 503 rather than an empty 200 is load-bearing: the
           dashboard lane (d091448b) draws the layer ABSENT, with the
           predicate caption `GLM proxy: HTTP 503`, rather than drawing an
           empty layer that looks like fair weather.
    """
    now = now or datetime.now(timezone.utc)
    try:
        sat_code, mins, box = parse_query(sat, minutes, bbox)
    except BadRequest as exc:
        return 400, _plain_headers(), _err(str(exc))

    reader = readers.get(sat_code)
    if reader is None:
        return 503, _plain_headers(), _err(
            f"no GLM reader running for {sat_code}")
    if getattr(reader, "ticks", 0) < 1:
        return 503, _plain_headers(), _err(
            f"GLM reader for {sat_code} has not completed a pass yet")

    with reader.lock:
        window = reader.window
        selected = window.select(now, mins)
        files = window.files_in_window(now, mins)

    # BBOX FIRST, THEN THIN. Reversing these two lines is the whole defect
    # the parameter exists to avoid: thinning to 5,000 across the hemisphere
    # and *then* cutting to the view hands a quiet West a nearly empty layer
    # whenever the Midwest is active, while the window held plenty of
    # Western flashes. Filtering first spends the cap on the area asked for.
    available = filter_by_bbox(selected, box)

    # `X-GLM-Newest` is the newest flash the CLIENT IS BEING SHOWN, so it is
    # taken after the bbox — reporting the hemisphere's newest flash on a
    # response that does not contain it is exactly the receipt-that-lies
    # ruling S-3 exists to stop. With no bbox this is identical to before.
    newest = max((f.t for f in available), default=None)

    shown = thin_by_energy(available, THIN_CAP)
    headers = build_receipt_headers(
        sat=sat_code, minutes=mins, newest=newest, files=files,
        returned=len(shown), available=len(available), bbox=box,
    )
    return 200, headers, geojson_bytes(flashes_to_geojson(shown, sat_code))


def _plain_headers() -> dict[str, str]:
    """Errors carry the CORS header too — a 503 the browser cannot read is
    indistinguishable from a network failure, and the dashboard's predicate
    caption needs the status code to name it."""
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-store",
    }


def _err(detail: str) -> bytes:
    import json
    return json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")


# ───────────────────────────────────────────────────────────────────────────
# The FastAPI binding — for `energylake-api`, not for this repo
# ───────────────────────────────────────────────────────────────────────────

def build_router(readers: Mapping[str, "object"]):
    """An `APIRouter` serving `/sky/glm` and `/sky/glm/health`.

    Imported lazily so that `sky.glm_route` stays importable — and its
    handler stays testable — in a process with no FastAPI, which is every
    process in `energylake-pantry`.

    Mounted in this repo's `main.py` — see `SKY / GLM lightning proxy` there
    for the readers dict, the lifespan start/stop, and the CORS exemption
    that makes the `ACAO: *` above survive to the wire.
    """
    from fastapi import APIRouter, Request, Response

    router = APIRouter(tags=["sky"])

    # `response_model=None` is load-bearing, not boilerplate: with a
    # `-> Response` return annotation and no override, FastAPI treats
    # `Response` as a response MODEL and every request 422s before the
    # handler runs. Measured on fastapi 0.141.1 and 0.115.6 alike.
    @router.get("/sky/glm", response_model=None)
    async def glm(request: Request) -> Response:
        status, headers, body = serve_glm(
            readers,
            sat=request.query_params.get("sat"),
            minutes=request.query_params.get("minutes"),
            bbox=request.query_params.get("bbox"),
        )
        media = headers.pop("Content-Type", "application/geo+json")
        return Response(content=body, status_code=status,
                        media_type=media, headers=headers)

    @router.get("/sky/glm/health", response_model=None)
    async def glm_health() -> dict:
        return {"readers": [r.health() for r in readers.values()]}

    return router
