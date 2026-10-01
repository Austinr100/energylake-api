"""
Bank the Local body for Los Angeles built from the hand-built LA heat-wave NWS
bodies (d091546 §2.5).

    python docs/receipts/local-phrase-d091546/bank.py          # writes la_local_body.json
    python docs/receipts/local-phrase-d091546/bank.py --check  # exit 1 if stale

The NWS bodies are tests/fixtures/nws/la_heat/ (scripts/build_nws_la_heat_fixture.py):
HAND-BUILT, not recorded — api.weather.gov is refused by the build session's
egress. The Local body is what `nws_arm.build` + `local_forecast.build_payload`
make of them at generated_at 2026-10-01T13:15Z, the route's own two calls,
with the memo receipts set as a cold read would set them.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import local_forecast as lf  # noqa: E402
import nws_arm  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "nws" / "la_heat"
OUT = Path(__file__).resolve().parent / "la_local_body.json"
LAT, LON = 34.052, -118.244
GENERATED_AT = datetime(2026, 10, 1, 13, 15, tzinfo=timezone.utc)


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def body() -> dict:
    points = _load("points.json")
    p = points["properties"]
    bundle = {
        "points": points, "grid": f"{p['gridId']}/{p['gridX']},{p['gridY']}",
        "tz": p["timeZone"],
        "station": _load("stations.json")["features"][0]["properties"]["stationIdentifier"],
        "hourly": _load("forecastHourly.us.json"), "forecast": _load("forecast.us.json"),
        "obs": _load("latest.json"), "alerts": _load("alerts.json"),
        "obs_error": None, "alerts_error": None,
        "memo": {"points": "miss", "forecast": "miss", "obs": "miss", "alerts": "miss"},
    }
    parts = nws_arm.build(bundle, LAT, LON, GENERATED_AT, [])
    return lf.build_payload(**parts)


def text() -> str:
    return json.dumps(body(), indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if OUT.exists() and OUT.read_text() == text() else 1)
    OUT.write_text(text())
