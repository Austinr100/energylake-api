"""Gate 0 cold reproduction (d091513). One US request (LAX fixture point) against
the ASGI app in-process, every memo empty, NWS answered instantly by the test
fixtures, and a sidecar store whose every GET sleeps 10 s and then raises
httpx.ReadTimeout -- the production shape of SidecarStore(timeout=10.0).
With REPRO_STORE=answer every GET sleeps 10 s and then answers (the bank fake).
Usage: python scripts/repro_local_cold_store.py [repo root, default .]"""
import asyncio, os, sys, time
root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
sys.path[:0] = [root, os.path.join(root, "tests")]
os.chdir(root)
import httpx
from fastapi.testclient import TestClient
import main, model_arm, nws_arm, weather_point as wp, local_forecast as lf
import test_local_forecast as t

BANK = t.FakeGlobalBank()


async def slow_store(key, byte_range):
    await asyncio.sleep(10.0)
    if os.environ.get("REPRO_STORE") == "answer":
        return await BANK(key, byte_range)
    raise httpx.ReadTimeout("fake store: 10 s read timeout")

async def runs():
    return [t.RUN]

main._local_nws_client = nws_arm.NwsClient(transport=t.FakeNws())
store = wp.SidecarStore(transport=slow_store)
main._get_weather_store = lambda: store
main._local_gfs_run_candidates = runs
lf.utcnow = lambda: t.NOW
model_arm._run_memo.clear()
getattr(model_arm, "_cell_memo", {}).clear()
http = TestClient(main.app)
t0 = time.perf_counter()
r = http.get("/api/local/forecast", params=t.LAX)
wall = time.perf_counter() - t0
b = r.json()
print(f"status {r.status_code}  wall {wall:.2f} s  daily rows {len(b['daily'])}")
print("notes:", [n for n in b["receipts"]["notes"] if n.startswith("days")])
print("Server-Timing:", r.headers["server-timing"])
