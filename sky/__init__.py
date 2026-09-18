"""Sky — the live-imagery room's pantry-side plumbing.

Today this package holds exactly one concern: the GLM lightning proxy
(lane d091448a). GLM is the only Sky source that needs a server at all —
STAR, IEM and NWS all answer `Access-Control-Allow-Origin: *` and are read
straight from the browser. The GOES NODD *listing* answers ACAO `*`; the
NetCDF *objects* do not (measured, `docs/receipts/sky-recon/hosted_run_35364902190.md`),
so a browser cannot read a GLM file cross-origin and something server-side
has to.

Ruling S-5 governs what that server is allowed to be: **pass-through with a
rolling window, never a bank.** Nothing here writes to Neon or R2.

────────────────────────────────────────────────────────────────────────────
VENDORED, NOT IMPORTED. This package was written and measured in
`energylake-pantry` (lane d091448a, commit 9002595) and copied here by lane
d091448m. It is vendored because this repo has no code dependency on the
pantry at all — the two share a Neon database, not a package: there is no
git dependency in `requirements.txt`, no submodule, no path import. Gate 0
also found the pantry serves no HTTP routes and cannot, so the route was
always going to be mounted here.

What changed on the way in, and nothing else did:

  * `?bbox=w,s,e,n` — `BBox`, `parse_bbox`, `filter_by_bbox`, `format_bbox`
    in `glm.py`, applied before thinning in `glm_route.serve_glm`, echoed as
    `X-GLM-Bbox`.
  * The module docstrings in `glm_route.py` now record that the `ACAO: *`
    inversion the pantry lane handed back is fixed in `main.py`.

Re-vendoring: diff against the pantry's `sky/` first; these two changes are
the whole delta.
"""

from sky.glm import (  # noqa: F401
    GLM_SATELLITES,
    WINDOW_MAX_MINUTES,
    THIN_CAP,
    BBox,
    Flash,
    GLMKey,
    GLMParseError,
    FlashWindow,
    parse_glm_key,
    hour_prefix,
    read_flashes,
    parse_bbox,
    filter_by_bbox,
    format_bbox,
    thin_by_energy,
    flashes_to_geojson,
    geojson_bytes,
    build_receipt_headers,
)

__all__ = [
    "GLM_SATELLITES",
    "WINDOW_MAX_MINUTES",
    "THIN_CAP",
    "BBox",
    "Flash",
    "GLMKey",
    "GLMParseError",
    "FlashWindow",
    "parse_glm_key",
    "hour_prefix",
    "read_flashes",
    "parse_bbox",
    "filter_by_bbox",
    "format_bbox",
    "thin_by_energy",
    "flashes_to_geojson",
    "geojson_bytes",
    "build_receipt_headers",
]
