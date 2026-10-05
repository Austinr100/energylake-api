"""The sample body: production's rows (tests/fixtures/mjo_status_d091614) through the route.

    PYTHONPATH=tests python docs/receipts/mjo-status-api-d091614/sample.py
"""
import json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2])); sys.path.insert(0, str(HERE.parents[2] / "tests"))

from fastapi.testclient import TestClient
import main
import test_mjo_status as t

main._utcnow = lambda: t.NOW
main._pool = t.bank_pool(t.production_bank())
body = TestClient(main.app).get("/api/mjo/status").json()
body["cache"] = {k: ("<varies>" if k in ("built_at", "build_seconds") else v) for k, v in body["cache"].items()}
(HERE / "sample_status.json").write_text(json.dumps(body, indent=2) + "\n")
print(json.dumps(body, indent=2)[:1500])
