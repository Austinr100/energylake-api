"""Tests for the GLM lightning proxy as MOUNTED IN THIS SERVICE (lane d091448m).

The arithmetic — the key grammar, the NetCDF parse, the window, the thinning —
is tested in `energylake-pantry`'s `tests/test_sky_glm.py` (116 checks), where
it was written. This file does not re-test it. It tests the three things that
are true only here, in the app the route is actually mounted in:

  §1  the mount itself — both routes exist, and the 503/200 contract holds
  §2  THE `ACAO: *` INVERSION the pantry lane handed back, all three rows
  §3  the same three rows on the error paths, which carry CORS too
  §4  the control: `/api/*` is UNCHANGED, still credentialed, still echoing
  §5  preflight on an exempt path
  §6  `?bbox=`, and the one thing about it that matters: it filters BEFORE
      thinning

No network and no database. The readers are replaced with a stub holding a
hand-built window, so nothing here touches NODD or Neon.
"""

import hashlib
import json
import os
import pathlib
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from sky import glm
from sky.glm import (
    THIN_CAP,
    Flash,
    FlashWindow,
    GLMKey,
    GLMParseError,
    read_flashes,
)

# The origin the production allowlist actually carries. The test environment's
# ALLOWED_ORIGINS defaults to http://localhost:3000, so §2 asserts this named
# origin against a purpose-built app wired exactly like `main.app` (below),
# and asserts the same three rows against the real app using the allowlist the
# real app really has. Both, because "an allowlisted origin" is the mechanism
# and "energylake.io" is the one the dashboard ships from.
PROD_ORIGIN = "https://energylake.io"
FOREIGN_ORIGIN = "https://anyone.else"

# Matches main.VERCEL_PREVIEW_ORIGIN_REGEX, so it is allowlisted in EVERY
# environment regardless of how ALLOWED_ORIGINS is set.
PREVIEW_ORIGIN = (
    "https://energylake-ma08ur41o-austinrodriguez221-6328s-projects.vercel.app"
)


# ───────────────────────────────────────────────────────────────────────────
# A reader stub — `.lock`, `.window`, `.ticks`, `.health()`, nothing else
# ───────────────────────────────────────────────────────────────────────────

class StubReader:
    def __init__(self, sat="goes19", ticks=1, flashes=(), t0=None):
        self.sat = sat
        self.lock = threading.RLock()
        self.window = FlashWindow(sat)
        self.ticks = ticks
        if flashes:
            t0 = t0 or datetime.now(timezone.utc)
            self.window.add_file(
                GLMKey(
                    key=("GLM-L2-LCFA/2026/261/20/OR_GLM-L2-LCFA_G19"
                         "_s20262612000000_e20262612000200_c20262612000221.nc"),
                    sat=sat,
                    start=t0 - timedelta(seconds=20),
                    end=t0,
                    created=t0,
                ),
                list(flashes),
            )

    def health(self):
        return {"sat": self.sat, "ticks": self.ticks,
                "flashes": len(self.window)}


def _flash(lat, lon, energy=1e-14, ago_s=5):
    return Flash(
        lat=lat, lon=lon,
        t=datetime.now(timezone.utc) - timedelta(seconds=ago_s),
        energy_j=energy, area_km2=100.0,
    )


@pytest.fixture
def readers():
    """Swap the live readers for stubs, IN PLACE.

    In place because `build_router` closed over this exact dict object when
    the app was built at import time; rebinding `main._SKY_GLM_READERS` would
    leave the router pointing at the old one and the test would pass against
    nothing.
    """
    original = dict(main._SKY_GLM_READERS)
    main._SKY_GLM_READERS.clear()
    yield main._SKY_GLM_READERS
    main._SKY_GLM_READERS.clear()
    main._SKY_GLM_READERS.update(original)


@pytest.fixture
def client():
    # No `with` block => lifespan does not run => no pool, no reader threads.
    return TestClient(main.app)


def _acao(resp):
    return resp.headers.get("access-control-allow-origin")


# ───────────────────────────────────────────────────────────────────────────
# §1 — the mount
# ───────────────────────────────────────────────────────────────────────────

def test_both_routes_are_mounted():
    paths = {r.path for r in main.app.routes if hasattr(r, "path")}
    assert "/sky/glm" in paths
    assert "/sky/glm/health" in paths


def test_no_reader_is_503_not_404(client, readers):
    """With no reader the answer is a truthful 503, never a 404.

    The dashboard draws the layer ABSENT with the predicate caption
    `GLM proxy: HTTP 503`. A 404 would read as a bad deploy instead.
    """
    resp = client.get("/sky/glm?sat=goes19")
    assert resp.status_code == 503
    assert "no GLM reader running" in resp.json()["detail"]


def test_reader_that_has_not_ticked_is_503(client, readers):
    readers["goes19"] = StubReader(ticks=0)
    resp = client.get("/sky/glm?sat=goes19")
    assert resp.status_code == 503
    assert "has not completed a pass" in resp.json()["detail"]


def test_empty_window_is_200_not_503(client, readers):
    """Zero flashes is the normal state of the sky, not an error."""
    readers["goes19"] = StubReader(ticks=3)
    resp = client.get("/sky/glm?sat=goes19")
    assert resp.status_code == 200
    assert resp.json()["features"] == []
    assert resp.headers["x-glm-newest"] == "-"


def test_health_reports_every_reader(client, readers):
    readers["goes19"] = StubReader("goes19")
    readers["goes18"] = StubReader("goes18")
    body = client.get("/sky/glm/health").json()
    assert {r["sat"] for r in body["readers"]} == {"goes19", "goes18"}


def test_receipt_headers_are_all_present(client, readers):
    readers["goes19"] = StubReader(flashes=[_flash(35.0, -100.0)])
    resp = client.get("/sky/glm?sat=goes19&minutes=5")
    assert resp.status_code == 200
    assert resp.headers["x-glm-window"] == "5m"
    assert resp.headers["x-glm-sat"] == "goes19"
    assert resp.headers["x-glm-files"] == "1"
    assert resp.headers["x-glm-thinned"] == "1/1"
    assert resp.headers["x-glm-bbox"] == "-"
    assert resp.headers["x-glm-newest"].endswith("Z")
    assert resp.headers["cache-control"] == "public, max-age=10"


def test_minutes_out_of_range_is_400_not_clamped(client, readers):
    readers["goes19"] = StubReader()
    resp = client.get("/sky/glm?sat=goes19&minutes=60")
    assert resp.status_code == 400
    assert "minutes must be 1..15" in resp.json()["detail"]


# ───────────────────────────────────────────────────────────────────────────
# §2 — THE FINDING. All three rows, against the app as it really is.
# ───────────────────────────────────────────────────────────────────────────
# Before the exemption, the third row answered with the ECHOED origin, because
# CORSMiddleware(allow_credentials=True) overwrites whatever the route set.
# `*` held for everyone EXCEPT the origins we trust. These assert it does not.

def test_acao_star_row1_no_origin(client, readers):
    readers["goes19"] = StubReader(flashes=[_flash(35.0, -100.0)])
    resp = client.get("/sky/glm?sat=goes19")
    assert resp.status_code == 200
    assert _acao(resp) == "*"


def test_acao_star_row2_foreign_origin(client, readers):
    readers["goes19"] = StubReader(flashes=[_flash(35.0, -100.0)])
    resp = client.get("/sky/glm?sat=goes19", headers={"Origin": FOREIGN_ORIGIN})
    assert resp.status_code == 200
    assert _acao(resp) == "*"


def test_acao_star_row3_allowlisted_origin(client, readers):
    """THE ROW THAT WAS WRONG. An allowlisted origin now gets `*` too.

    `PREVIEW_ORIGIN` matches `main.VERCEL_PREVIEW_ORIGIN_REGEX`, so it is
    allowlisted in every environment — including this one, where
    ALLOWED_ORIGINS is just the localhost default. Without the exemption the
    middleware would echo it back and add `Vary: Origin`.
    """
    readers["goes19"] = StubReader(flashes=[_flash(35.0, -100.0)])
    resp = client.get("/sky/glm?sat=goes19", headers={"Origin": PREVIEW_ORIGIN})
    assert resp.status_code == 200
    assert _acao(resp) == "*"
    assert _acao(resp) != PREVIEW_ORIGIN
    # `*` does not depend on the origin, so nothing should fragment the cache
    # that `Cache-Control: public, max-age=10` is asking for.
    assert "origin" not in resp.headers.get("vary", "").lower()
    # `*` and credentials are mutually exclusive; a browser rejects the pair.
    assert "access-control-allow-credentials" not in resp.headers


def test_acao_star_row3_with_the_named_production_origin(readers):
    """The same row again, with `https://energylake.io` by name.

    The test environment's ALLOWED_ORIGINS is the localhost default, so the
    production origin is asserted against an app wired exactly as `main.app`
    is — same middleware class, same flags — but with the allowlist the
    service really carries in Railway.
    """
    readers["goes19"] = StubReader(flashes=[_flash(35.0, -100.0)])
    app = FastAPI()
    app.add_middleware(
        main.SkyExemptCORSMiddleware,
        allow_origins=[PROD_ORIGIN, "https://www.energylake.io"],
        allow_origin_regex=main.VERCEL_PREVIEW_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(main._build_sky_glm_router(readers))

    @app.get("/api/control")
    def control():
        return {"ok": True}

    c = TestClient(app)

    # All three rows on /sky/*.
    assert _acao(c.get("/sky/glm?sat=goes19")) == "*"
    assert _acao(c.get("/sky/glm?sat=goes19",
                       headers={"Origin": FOREIGN_ORIGIN})) == "*"
    resp = c.get("/sky/glm?sat=goes19", headers={"Origin": PROD_ORIGIN})
    assert resp.status_code == 200
    assert _acao(resp) == "*", (
        "energylake.io must get `*` on /sky/*, not an echoed origin")

    # And the control: the SAME app still echoes on a non-exempt path, which
    # is what proves the exemption is path-scoped and not a global declaw.
    ctrl = c.get("/api/control", headers={"Origin": PROD_ORIGIN})
    assert _acao(ctrl) == PROD_ORIGIN
    assert ctrl.headers.get("access-control-allow-credentials") == "true"


# ───────────────────────────────────────────────────────────────────────────
# §3 — the error paths carry CORS too
# ───────────────────────────────────────────────────────────────────────────
# A 503 the browser cannot read is indistinguishable from a network failure,
# and the dashboard's predicate caption needs the status code to name it.

@pytest.mark.parametrize("origin", [None, FOREIGN_ORIGIN, PREVIEW_ORIGIN])
def test_503_carries_acao_star_for_every_origin(client, readers, origin):
    headers = {"Origin": origin} if origin else {}
    resp = client.get("/sky/glm?sat=goes19", headers=headers)
    assert resp.status_code == 503
    assert _acao(resp) == "*"


@pytest.mark.parametrize("origin", [None, FOREIGN_ORIGIN, PREVIEW_ORIGIN])
def test_400_carries_acao_star_for_every_origin(client, readers, origin):
    readers["goes19"] = StubReader()
    headers = {"Origin": origin} if origin else {}
    resp = client.get("/sky/glm?sat=goes19&minutes=99", headers=headers)
    assert resp.status_code == 400
    assert _acao(resp) == "*"


# ───────────────────────────────────────────────────────────────────────────
# §4 — the control: /api/* is untouched
# ───────────────────────────────────────────────────────────────────────────

def test_api_routes_still_echo_the_allowlisted_origin(client):
    """The exemption must not have declawed CORS for the other 73 routes.

    A preflight, so no route handler and no database pool are touched — the
    same technique `tests/test_cors.py` uses.
    """
    resp = client.options(
        "/api/tape/recent",
        headers={"Origin": PREVIEW_ORIGIN,
                 "Access-Control-Request-Method": "GET"},
    )
    assert _acao(resp) == PREVIEW_ORIGIN
    assert resp.headers.get("access-control-allow-credentials") == "true"


def test_api_preflight_still_rejects_a_foreign_origin(client):
    resp = client.options(
        "/api/tape/recent",
        headers={"Origin": FOREIGN_ORIGIN,
                 "Access-Control-Request-Method": "GET"},
    )
    assert _acao(resp) != FOREIGN_ORIGIN


def test_exemption_is_prefix_scoped_not_substring(client):
    """`/sky/` must not accidentally exempt a lookalike path."""
    mw = main.SkyExemptCORSMiddleware(
        app=None, allow_origins=[], allow_credentials=True)
    assert mw._is_exempt({"type": "http", "path": "/sky/glm"})
    assert mw._is_exempt({"type": "http", "path": "/sky/glm/health"})
    assert not mw._is_exempt({"type": "http", "path": "/skywalker"})
    assert not mw._is_exempt({"type": "http", "path": "/api/sky/glm"})
    assert not mw._is_exempt({"type": "websocket", "path": "/sky/glm"})


def test_a_mistyped_satellite_env_var_does_not_brick_the_service():
    """A misconfigured sky costs the sky, not the service.

    `GLMReader` rejects an unknown satellite in its constructor — correctly —
    but that runs at import time, so an unguarded typo in SKY_GLM_SATELLITES
    would take all 73 `/api/*` routes down with it.
    """
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c",
         "import main;"
         "print(sorted(main._SKY_GLM_READERS));"
         "print(any(getattr(r, 'path', '') == '/sky/glm' for r in main.app.routes))"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "SKY_GLM_SATELLITES": "goes19,goes77"},
        cwd=pathlib.Path(__file__).parent.parent,
    )
    assert proc.returncode == 0, f"import died: {proc.stderr[-2000:]}"
    lines = proc.stdout.strip().splitlines()
    assert lines[0] == "['goes19']", "the unknown bird must be dropped"
    assert lines[1] == "True", "the route must still be mounted"


# ───────────────────────────────────────────────────────────────────────────
# §5 — preflight on an exempt path
# ───────────────────────────────────────────────────────────────────────────

def test_preflight_on_sky_is_answered_with_star(client, readers):
    """With the middleware bypassed there is no OPTIONS route; we answer it."""
    resp = client.options(
        "/sky/glm",
        headers={"Origin": PROD_ORIGIN,
                 "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code == 200
    assert _acao(resp) == "*"
    assert "GET" in resp.headers["access-control-allow-methods"]
    assert "access-control-allow-credentials" not in resp.headers


def test_preflight_mirrors_requested_headers(client, readers):
    resp = client.options(
        "/sky/glm",
        headers={"Origin": PROD_ORIGIN,
                 "Access-Control-Request-Method": "GET",
                 "Access-Control-Request-Headers": "x-request-id"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-headers"] == "x-request-id"


# ───────────────────────────────────────────────────────────────────────────
# §6 — ?bbox=w,s,e,n
# ───────────────────────────────────────────────────────────────────────────

def test_bbox_filters_and_is_echoed(client, readers):
    readers["goes19"] = StubReader(flashes=[
        _flash(35.0, -100.0),   # CONUS, inside
        _flash(40.0, -120.0),   # West, inside
        _flash(-10.0, -60.0),   # South America, outside
        _flash(50.0, 10.0),     # Europe, outside
    ])
    resp = client.get("/sky/glm?sat=goes19&bbox=-125,25,-95,45")
    assert resp.status_code == 200
    feats = resp.json()["features"]
    assert len(feats) == 2
    assert resp.headers["x-glm-bbox"] == "-125,25,-95,45"
    assert resp.headers["x-glm-thinned"] == "2/2"


def test_bbox_absent_means_no_filter(client, readers):
    readers["goes19"] = StubReader(flashes=[
        _flash(35.0, -100.0), _flash(50.0, 10.0)])
    resp = client.get("/sky/glm?sat=goes19")
    assert len(resp.json()["features"]) == 2
    assert resp.headers["x-glm-bbox"] == "-"


def test_bbox_is_inclusive_on_its_edges(client, readers):
    readers["goes19"] = StubReader(flashes=[_flash(25.0, -125.0)])
    resp = client.get("/sky/glm?sat=goes19&bbox=-125,25,-95,45")
    assert len(resp.json()["features"]) == 1


def test_bbox_crossing_the_antimeridian(client, readers):
    """`w > e` wraps. Real for GOES-18, whose view spans 180°."""
    readers["goes19"] = StubReader(flashes=[
        _flash(10.0, 175.0),    # east of w, inside the wrap
        _flash(10.0, -175.0),   # west of e, inside the wrap
        _flash(10.0, 0.0),      # the long way round, outside
    ])
    resp = client.get("/sky/glm?sat=goes19&bbox=170,0,-150,30")
    assert resp.status_code == 200
    assert len(resp.json()["features"]) == 2
    assert resp.headers["x-glm-bbox"] == "170,0,-150,30"


def test_bbox_filters_BEFORE_thinning(client, readers):
    """THE POINT OF THE PARAMETER, and the one ordering that can be wrong.

    The window holds `THIN_CAP` very energetic flashes over the Midwest and a
    handful of weak ones over the West. Thin-then-filter would spend the
    entire cap on the Midwest and hand a `bbox` over the West an EMPTY layer,
    while the window plainly held Western flashes. Filter-then-thin returns
    them all.
    """
    midwest = [_flash(40.0, -90.0, energy=1e-12) for _ in range(THIN_CAP)]
    west = [_flash(38.0, -120.0, energy=1e-16) for _ in range(7)]
    readers["goes19"] = StubReader(flashes=midwest + west)

    resp = client.get("/sky/glm?sat=goes19&bbox=-125,32,-114,42")
    assert resp.status_code == 200
    feats = resp.json()["features"]
    assert len(feats) == 7, (
        "bbox must be applied before thinning; thin-first would return 0 here")
    assert resp.headers["x-glm-thinned"] == "7/7"
    assert all(f["geometry"]["coordinates"][0] == -120.0 for f in feats)


def test_thinning_still_caps_within_a_bbox(client, readers):
    readers["goes19"] = StubReader(flashes=[
        _flash(38.0, -120.0, energy=1e-12) for _ in range(THIN_CAP + 500)])
    resp = client.get("/sky/glm?sat=goes19&bbox=-125,32,-114,42")
    assert len(resp.json()["features"]) == THIN_CAP
    assert resp.headers["x-glm-thinned"] == f"{THIN_CAP}/{THIN_CAP + 500}"


def test_newest_header_is_taken_after_the_bbox(client, readers):
    """`X-GLM-Newest` describes what the client was SHOWN.

    Reporting the hemisphere's newest flash on a response that does not
    contain it is the receipt-that-lies ruling S-3 exists to stop.
    """
    readers["goes19"] = StubReader(flashes=[
        _flash(38.0, -120.0, ago_s=300),   # in the box, older
        _flash(-10.0, -60.0, ago_s=1),     # outside the box, newest overall
    ])
    inside = client.get("/sky/glm?sat=goes19&bbox=-125,32,-114,42")
    everything = client.get("/sky/glm?sat=goes19")
    assert inside.headers["x-glm-newest"] != everything.headers["x-glm-newest"]


@pytest.mark.parametrize("bad,msg", [
    ("1,2,3", "four comma-separated numbers"),
    ("a,b,c,d", "four numbers"),
    ("-125,45,-95,25", "must be below north"),
    ("-200,25,-95,45", "w=-200.0 out of range -180..180"),
    ("-125,25,190,45", "e=190.0 out of range -180..180"),
    ("-125,-95,-95,45", "s=-95.0 out of range -90..90"),
    ("-125,25,-95,95", "n=95.0 out of range -90..90"),
    ("-125,25,-125,45", "west equals east"),
])
def test_bad_bbox_is_400_naming_the_fault(client, readers, bad, msg):
    """Refused, never repaired — a silently fixed box would be stamped onto
    `X-GLM-Bbox` as if it had been the one asked for."""
    readers["goes19"] = StubReader()
    resp = client.get(f"/sky/glm?sat=goes19&bbox={bad}")
    assert resp.status_code == 400
    assert msg in resp.json()["detail"]


# ───────────────────────────────────────────────────────────────────────────
# §8 — THE RECEIPTS ARE ON THE WIRE; THESE ARE ABOUT WHETHER THEY ARE READABLE
# ───────────────────────────────────────────────────────────────────────────
# Lane d091453. `ACAO: *` (§2) hands the page the BODY. It does not let the
# page read one of the six `X-GLM-*` headers: only the CORS-safelisted
# response headers reach a cross-origin `fetch`, and the rest are dropped on
# a response that is `res.ok` and byte-complete. Measured in chromium by
# d091448b: 0 of 6 readable, 6 of 6 with `Access-Control-Expose-Headers`.
#
# WHAT THESE TESTS DO NOT PROVE. Every assertion below is a string on a
# response. A browser's CORS filter is not in this process and cannot be —
# the acceptance test is the dashboard receipt flipping from its `blocked`
# shape to its `exposed` shape with no dashboard change, taken after deploy.
# Read a green §8 as "the string is present and derived", nothing more.

#: The six as they stand today. Named so the set assertions below cannot pass
#: vacuously if the receipt headers ever stop being sent at all — but never
#: re-typed as the exposed list, which is derived (see §8's second test).
SIX_RECEIPT_HEADERS = {
    "x-glm-window", "x-glm-newest", "x-glm-files",
    "x-glm-thinned", "x-glm-sat", "x-glm-bbox",
}


def _exposed(resp):
    """The `Access-Control-Expose-Headers` value as a lowercase set."""
    raw = resp.headers.get("access-control-expose-headers", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _receipts_sent(resp):
    return {k.lower() for k in resp.headers if k.lower().startswith("x-glm-")}


def test_exposed_list_is_exactly_the_receipt_headers_actually_sent(
        client, readers):
    """THE GUARD THE SPEC ASKS FOR: a seventh `X-GLM-*` header that is sent
    and not exposed fails here, because the two sets are compared rather
    than a fixed list being spot-checked."""
    readers["goes19"] = StubReader(flashes=[_flash(35.0, -100.0)])
    resp = client.get("/sky/glm?sat=goes19&minutes=5")
    assert resp.status_code == 200
    sent = _receipts_sent(resp)
    assert SIX_RECEIPT_HEADERS <= sent, "the receipt headers themselves are gone"
    assert _exposed(resp) == sent


def test_the_exposed_list_is_derived_not_typed(client, readers):
    """A seventh receipt header is exposed BY THE ACT OF ADDING IT.

    `build_expose_headers` reads the dict it is handed, so this passes a
    dict carrying a seventh name that exists nowhere in the source. A
    hand-typed list of six would fail this and could not be made to pass
    without editing a second place — which is the whole point.
    """
    sent = glm.build_receipt_headers(
        sat="goes19", minutes=5, newest=None, files=1,
        returned=0, available=0, bbox=None)
    assert glm.build_expose_headers(sent) == sent["Access-Control-Expose-Headers"]

    seventh = {**sent, "X-GLM-Seventh": "whatever"}
    assert "X-GLM-Seventh" in glm.build_expose_headers(seventh)
    assert set(glm.build_expose_headers(seventh).split(", ")) == {
        k for k in seventh if k.startswith("X-GLM-")}


def test_nothing_but_the_receipt_headers_is_exposed(client, readers):
    """`Content-Type` and `Cache-Control` are CORS-safelisted already, and
    listing headers the page can read anyway would make the receipt look
    like it governs more than it does.

    THE CENSUS LIVES HERE AND NOWHERE ELSE. The other §8 tests compare the
    exposed list against whatever was sent, so a seventh receipt header is
    exposed silently and correctly; this one line is where a human has to
    agree that there are now seven.
    """
    readers["goes19"] = StubReader(flashes=[_flash(35.0, -100.0)])
    exposed = _exposed(client.get("/sky/glm?sat=goes19"))
    assert exposed == SIX_RECEIPT_HEADERS
    assert not {"content-type", "cache-control", "vary"} & exposed


@pytest.mark.parametrize("origin", [None, FOREIGN_ORIGIN, PREVIEW_ORIGIN])
def test_the_three_cors_rows_still_hold_and_now_expose(client, readers, origin):
    """§2's three rows re-measured with the new header beside them: the
    exemption still answers `*` for all three, and all three can read the
    receipts. One is no use without the other."""
    readers["goes19"] = StubReader(flashes=[_flash(35.0, -100.0)])
    headers = {"Origin": origin} if origin else {}
    resp = client.get("/sky/glm?sat=goes19", headers=headers)
    assert resp.status_code == 200
    assert _acao(resp) == "*"
    assert "access-control-allow-credentials" not in resp.headers
    assert _exposed(resp) == _receipts_sent(resp) >= SIX_RECEIPT_HEADERS


def test_the_expose_header_comes_from_the_route_not_the_middleware(readers):
    """WHERE THE CONTRACT LIVES, asserted rather than asserted-in-prose.

    The router mounted in a bare app with NO CORS middleware at all still
    sends the expose header — so it is the route's, and
    `SkyExemptCORSMiddleware` (whose job is to keep the credentialed
    app-wide CORS off `/sky/*`) contributes nothing to it. If someone ever
    moves the contract into the middleware, this is the test that notices.
    """
    readers["goes19"] = StubReader(flashes=[_flash(35.0, -100.0)])
    bare = FastAPI()
    bare.include_router(main._build_sky_glm_router(readers))
    resp = TestClient(bare).get("/sky/glm?sat=goes19",
                                headers={"Origin": PROD_ORIGIN})
    assert resp.status_code == 200
    assert _exposed(resp) == _receipts_sent(resp) >= SIX_RECEIPT_HEADERS
    assert resp.headers["timing-allow-origin"] == "*"

    src = pathlib.Path(main.__file__).read_text()
    mw = src[src.index("class SkyExemptCORSMiddleware"):
             src.index("app.add_middleware(\n    SkyExemptCORSMiddleware")]
    assert "Expose-Headers" not in mw
    assert "Timing-Allow-Origin" not in mw


# `Timing-Allow-Origin` — on /sky/* only, and the reason is specific to it.

def test_timing_allow_origin_on_the_200(client, readers):
    readers["goes19"] = StubReader(flashes=[_flash(35.0, -100.0)])
    resp = client.get("/sky/glm?sat=goes19")
    assert resp.headers["timing-allow-origin"] == "*"


@pytest.mark.parametrize("path,status", [
    ("/sky/glm?sat=goes19", 503),                 # no reader
    ("/sky/glm?sat=goes19&minutes=99", 400),      # out of contract
])
def test_timing_allow_origin_on_the_error_paths(client, readers, path, status):
    """A page weighing its own traffic has to be able to weigh the failures.
    The 400 needs a reader present to get past the 503, so it gets one."""
    if status == 400:
        readers["goes19"] = StubReader()
    resp = client.get(path)
    assert resp.status_code == status
    assert resp.headers["timing-allow-origin"] == "*"


def test_api_routes_get_neither_header(client):
    """THE LINE THAT MUST NOT MOVE. `/api/*` is credentialed; exposing
    headers or timing there discloses things a cross-origin page has no
    business reading. A preflight, so no handler and no pool are touched."""
    resp = client.options(
        "/api/tape/recent",
        headers={"Origin": PREVIEW_ORIGIN,
                 "Access-Control-Request-Method": "GET"},
    )
    assert _acao(resp) == PREVIEW_ORIGIN
    assert "timing-allow-origin" not in resp.headers
    assert "x-glm" not in resp.headers.get(
        "access-control-expose-headers", "").lower()


# ───────────────────────────────────────────────────────────────────────────
# §7 — netCDF4 IS NOT THREAD-SAFE, AND THIS SERVICE PARSES FROM TWO THREADS
# ───────────────────────────────────────────────────────────────────────────
# Found on the first real boot of the mounted route (lane d091448m): one
# reader thread per satellite, both parsing on the 20-s cadence, and uvicorn
# died with SIGSEGV inside the first tick —
#
#     sky/glm.py: ds = netCDF4.Dataset("glm-inmemory", "r", memory=blob)
#     RuntimeError: NetCDF: Can't open HDF5 attribute
#
# Reduced against the fixture below: 80 sequential parses are clean; 2 threads
# x 40 parses is exit 139. The wheel's HDF5 is not built `--enable-threadsafe`.
# `sky.glm._NETCDF4_LOCK` serialises the parse; see the comment on it.
#
# This is a MOUNT concern, not a parser concern — one reader is safe, and the
# pantry's single-threaded suite is right to pass without it.

FIXTURE = (pathlib.Path(__file__).parent / "fixtures" / "sky_glm"
           / "OR_GLM-L2-LCFA_G19_s20262611400000_e20262611400200"
             "_c20262611400221.nc")

netCDF4 = pytest.importorskip(
    "netCDF4",
    reason="netCDF4 is in requirements.txt; without it /sky/glm cannot parse")


@pytest.fixture(scope="module")
def blob():
    return FIXTURE.read_bytes()


def test_the_vendored_fixture_is_the_one_that_was_measured(blob):
    """Real, unmodified NOAA bytes — digest and census from FIXTURE.json."""
    meta = json.loads((FIXTURE.parent / "FIXTURE.json").read_text())
    assert len(blob) == meta["object"]["bytes"] == 259_006
    assert hashlib.sha256(blob).hexdigest() == meta["object"]["sha256"]
    assert blob[:8] == b"\x89HDF\r\n\x1a\n", "NetCDF-4 is HDF5 underneath"


def test_the_fixture_parses_to_its_measured_flash_count(blob):
    flashes = read_flashes(blob, key=FIXTURE.name)
    assert len(flashes) == 210        # FIXTURE.json measured_content
    assert all(f.t.tzinfo is not None for f in flashes)


def test_read_flashes_actually_takes_the_lock(blob):
    """THE GUARD, and it fails as an ASSERTION rather than as a crash.

    Holding `_NETCDF4_LOCK` here must stall a parse on another thread. If the
    lock is ever removed, this reports a clean failure — whereas the
    concurrency test below would report a SIGSEGV that takes pytest with it,
    which is a true signal but a miserable one to debug.
    """
    finished = threading.Event()

    def parse():
        try:
            read_flashes(blob, key=FIXTURE.name)
        finally:
            finished.set()

    with glm._NETCDF4_LOCK:
        t = threading.Thread(target=parse, daemon=True)
        t.start()
        # A parse is ~11 ms; 1.5 s is ~130x margin. Finishing while we hold
        # the lock can only mean it was never asked for.
        escaped = finished.wait(timeout=1.5)
    t.join(timeout=15)
    assert not escaped, "read_flashes parsed without acquiring _NETCDF4_LOCK"


def test_two_readers_parsing_concurrently_do_not_corrupt_hdf5(blob):
    """The shape the service actually runs: two satellites, two threads.

    Unlocked, this is the SIGSEGV. Locked, every parse returns the same 210
    flashes — the count is asserted per parse because silent truncation, not
    just a crash, is a way corrupted library state shows up.
    """
    counts, errors = [], []

    def work():
        for _ in range(12):
            try:
                counts.append(len(read_flashes(blob, key=FIXTURE.name)))
            except Exception as exc:  # noqa: BLE001 - the point is to catch any
                errors.append(repr(exc))

    threads = [threading.Thread(target=work) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == []
    assert len(counts) == 24
    assert set(counts) == {210}


def test_netcdf4_runtime_errors_become_GLMParseError():
    """netCDF4 raises RuntimeError for its own library faults.

    `read_flashes` promises `GLMParseError` for anything unreadable, and the
    reader's per-file handler catches only that. A RuntimeError escaping
    instead would skip `_failed`, so the key would be re-fetched every 20 s
    forever — the tight retry loop the spec forbids, reached via an exception
    type rather than a retry policy.
    """
    truncated = FIXTURE.read_bytes()[:40_000]
    with pytest.raises(GLMParseError):
        read_flashes(truncated, key="truncated.nc")
