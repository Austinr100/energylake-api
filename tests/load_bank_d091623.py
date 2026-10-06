"""d091623: a second production replay, taken after the gated writer.

tests/fixtures/load_outlook_d091623/production_2026_10_06_1240z.json holds
what each of load_outlook.py's statements returned on Neon at NOW (read-only,
through the Neon connector; docs/receipts/net-demand-reason-d091623/render.py
renders the exact text and bank.py wrote the file). It has d091611's form, so
load_bank_d091611's loader and pool answer from it unchanged. The 10-05
replay predates the writer's gate (pantry d091607): its rows beyond the fit
still carried figures. In this one they carry the line's id and no figure.

Not a test module (no test_ prefix).
"""

import pathlib
from datetime import datetime, timezone

import load_bank_d091611 as _lb

FIX = (pathlib.Path(__file__).resolve().parent / "fixtures" / "load_outlook_d091623"
       / "production_2026_10_06_1240z.json")
NOW = datetime(2026, 10, 6, 12, 40, tzinfo=timezone.utc)


def load() -> dict:
    return _lb.load(FIX)


pool = _lb.pool
