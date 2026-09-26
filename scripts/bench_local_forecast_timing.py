"""
Bench `/api/local/forecast` on the FAKE transports with injected latency, and
print each leg of the route's own `Server-Timing` (d091491 §2.1, D-09-25-27).

NO NETWORK. NWS is `tests/test_local_forecast.FakeNws`, the bank is its
`FakeGlobalBank`, run discovery is a stub. Each fake call sleeps first:

    NWS, per call (§0's direct read of an unasked Wichita gridpoint, ms):
        points 308, forecastHourly 318, forecast 173,
        observationStations 108, observations/latest 113, alerts 140
    bank, per sidecar GET (header or 4-byte range):  SIDECAR_MS
    run ledger query (the Neon read in `discover_run`): LEDGER_MS

These are assumptions for comparing code paths, NOT a model of production:
the bank figure is chosen so a serial cold model read lands near §0's ~2.5 s.

    python scripts/bench_local_forecast_timing.py [--n 5]

Cases: `us_cold` (fresh NWS memo, fresh run memo, fresh store), `us_warm`
(the same request again), `model_cold` (Vancouver, fresh), `model_warm`.
Each value is the median over `--n` repetitions.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

import local_forecast as lf  # noqa: E402
import main  # noqa: E402
import model_arm  # noqa: E402
import nws_arm  # noqa: E402
import weather_point as wp  # noqa: E402
import test_local_forecast as T  # noqa: E402

NWS_MS = {"points": 308, "hourly": 318, "forecast": 173, "stations": 108,
          "obs": 113, "alerts": 140}
SIDECAR_MS = 100
LEDGER_MS = 150


def _latent_nws(fake):
    async def call(url, params):
        kind = next(k for k, rx in fake.ROUTES if rx.search(url))
        await asyncio.sleep(NWS_MS[kind] / 1000.0)
        return await fake(url, params)
    return call


def _latent_bank(fake):
    async def call(key, byte_range):
        await asyncio.sleep(SIDECAR_MS / 1000.0)
        return await fake(key, byte_range)
    return call


async def _runs():
    await asyncio.sleep(LEDGER_MS / 1000.0)
    return [T.RUN]


def _parse(header: str) -> dict:
    out = {}
    for part in header.split(","):
        name, _, dur = part.strip().partition(";dur=")
        if name:
            out[name] = float(dur)
    return out


def _fresh():
    client = nws_arm.NwsClient(transport=_latent_nws(T.FakeNws()))
    store = wp.SidecarStore(transport=_latent_bank(T.FakeGlobalBank()))
    main._local_nws_client = client
    main._get_weather_store = lambda: store
    model_arm._run_memo.clear()
    getattr(model_arm, "_refresh_tasks", {}).clear()


def main_(n: int) -> None:
    main._local_gfs_run_candidates = _runs
    lf.utcnow = lambda: T.NOW
    http = TestClient(main.app)
    cases = {"us_cold": [], "us_warm": [], "model_cold": [], "model_warm": []}
    for _ in range(n):
        for arm, where in (("us", T.LAX), ("model", T.VANCOUVER)):
            _fresh()
            for temp in ("cold", "warm"):
                t0 = time.perf_counter()
                r = http.get("/api/local/forecast", params=where)
                wall = (time.perf_counter() - t0) * 1000.0
                assert r.status_code == 200, r.text
                legs = _parse(r.headers["server-timing"])
                legs["wall"] = wall
                cases[f"{arm}_{temp}"].append(legs)
    names = lf.TIMING_NAMES + ("wall",)
    print(f"| case | {' | '.join(names)} |")
    print(f"| --- |{' --- |' * len(names)}")
    for case, runs in cases.items():
        cells = []
        for k in names:
            vals = [r[k] for r in runs if k in r]
            cells.append(f"{statistics.median(vals):.1f}" if len(vals) == len(runs) else "—")
        print(f"| {case} | {' | '.join(cells)} |")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    main_(ap.parse_args().n)
