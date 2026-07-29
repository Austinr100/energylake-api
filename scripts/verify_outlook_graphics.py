#!/usr/bin/env python3
"""Build-time STOP-gate for the /api/weather/outlooks graphic registry.

The spec is explicit: every CPC product-image `url` in `main.OUTLOOK_GRAPHICS`
must be verified to resolve to a CURRENT image (HTTP 200 + `image/*`
content-type) before it is served live — "do not trust URL patterns from memory
or from this spec." This script IS that gate. It curls each url, records the
status + content-type, prints the verification log (acceptance receipt #2), and
emits the exact `_VERIFIED_GRAPHIC_IDS` set to paste back into main.py.

Run it from a network with egress to www.cpc.ncep.noaa.gov (the CI/build box or
the Railway service shell). The default Claude Code build environment's egress
policy blocks that host, so the gate cannot run there — that is why
`_VERIFIED_GRAPHIC_IDS` currently ships EMPTY and every graphic renders as
honest absence.

Usage:
    python scripts/verify_outlook_graphics.py
Exit code 0 iff every url returned 200 + image/*; non-zero otherwise (so it can
gate CI). No third-party deps — stdlib urllib only.
"""

from __future__ import annotations

import sys
import urllib.request

# Import the single source of truth rather than re-listing URLs here. Both
# registries are gated: OUTLOOK_GRAPHICS is the CPC shelf (keyed by outlook_type,
# the warehouse join key); SHELF_GRAPHICS is the non-CPC shelves (keyed by shelf
# id, no warehouse lane behind them).
sys.path.insert(0, ".")
from main import OUTLOOK_GRAPHICS, SHELF_GRAPHICS  # noqa: E402

TIMEOUT = 25


def registry_entries():
    """(group, graphic) for every gated url, CPC shelf first then the rest."""
    for product_id, graphics in OUTLOOK_GRAPHICS.items():
        for g in graphics:
            yield product_id, g
    for shelf_id, graphics in SHELF_GRAPHICS.items():
        for g in graphics:
            yield shelf_id, g


def check(url: str) -> tuple[int, str, bool]:
    """(status, content_type, ok). ok iff 200 and content-type starts image/."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "energylake-verify/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status = resp.status
            ctype = resp.headers.get("Content-Type", "") or ""
    except urllib.error.HTTPError as e:  # noqa: PERF203
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", False
    except Exception as e:  # network/DNS/TLS/egress-policy failures
        return 0, f"error: {e}", False
    ok = status == 200 and ctype.lower().startswith("image/")
    return status, ctype, ok


def main() -> int:
    verified: list[str] = []
    failed: list[str] = []
    print(f"{'graphic_id':<12} {'status':<6} {'content-type':<28} url")
    print("-" * 100)
    for _group, g in registry_entries():
        status, ctype, ok = check(g["url"])
        print(f"{g['graphic_id']:<12} {status:<6} {ctype[:28]:<28} {g['url']}")
        (verified if ok else failed).append(g["graphic_id"])

    print("-" * 100)
    print(f"verified: {len(verified)}  failed: {len(failed)}")
    if verified:
        ids = ", ".join(f'"{gid}"' for gid in verified)
        print("\nPaste into main.py:")
        print(f"_VERIFIED_GRAPHIC_IDS: set[str] = {{{ids}}}")
    if failed:
        print(f"\nFAILED (ship as source_url_unverified): {', '.join(failed)}")
        # STOP-gate #1: verification failing for >2 products is a hard stop.
        if len(failed) > 2:
            print("STOP-gate #1 tripped: >2 products failed verification — "
                  "stop, report, do not improvise alternate URL patterns.")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
