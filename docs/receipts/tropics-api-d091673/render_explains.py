"""Render every Tropics statement with the literal parameters its Neon plan was
taken with (d091673), by psycopg's own client-side binding, into explains.sql.

    python3 docs/receipts/tropics-api-d091673/render_explains.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from psycopg import ClientCursor  # noqa: E402

import load_bank_d091673 as lb  # noqa: E402
from test_tropics_d091673 import route_statements  # noqa: E402

out = ["-- d091673: every statement the Tropics routes run, with the literal parameters",
       "-- its Neon plan (plans.md) was taken with. Rendered by psycopg's ClientCursor.mogrify",
       "-- from tropics.py's own text; /tracks is also planned at n = 4.", ""]
with lb.cluster(load_bank=False) as conn:
    cur = ClientCursor(conn)
    for name, sql, params in route_statements():
        out += [f"-- {name} {params or ''}".rstrip(), cur.mogrify(sql, params).strip() + ";", ""]
(ROOT / "docs/receipts/tropics-api-d091673/explains.sql").write_text("\n".join(out))
