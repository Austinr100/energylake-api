"""d091741: every ledger read of the index routes, with real parameters, as literal SQL.

    python3 -I docs/receipts/tropics-api-index-d091741/explains_sql.py > docs/receipts/tropics-api-index-d091741/explains.sql

psycopg's own ClientCursor.mogrify over the routes' statements (tropics_index.py)
and parameter builders, on a throwaway local cluster (only to mogrify). Each was
then run on Neon as EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) through the Neon
connector (read-only): neon_plans.json. Not run by the suite.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import psycopg  # noqa: E402

import tropics as tr  # noqa: E402
import tropics_index as ti  # noqa: E402
import load_bank_d091673 as lb  # noqa: E402

STATEMENTS = [
    ("STORM_SQL ep202026", tr.STORM_SQL, {"storm_id": "ep202026"}),
    ("ENTRY_NEWEST_SQL ep202026", ti.ENTRY_NEWEST_SQL, {"storm_id": "ep202026"}),
    ("ENTRY_NEWEST_SQL ep182026", ti.ENTRY_NEWEST_SQL, {"storm_id": "ep182026"}),
    ("ADVISORY_AT_SQL ep202026 032", ti.ADVISORY_AT_SQL, {"storm_id": "ep202026", "advisory": "032"}),
    ("ADVISORY_NEWEST_SQL ep202026", ti.ADVISORY_NEWEST_SQL, {"storm_id": "ep202026"}),
    ("ENTRY_SQL ep182026 058", ti.ENTRY_SQL, ti.entry_params("ep182026", "058")),
    ("FILES_SQL products ep202026 032", ti.FILES_SQL,
     ti.files_params("ep202026", "032", ti.ADVISORY_PRODUCTS)),
    ("FILES_SQL wsp 2026101506", ti.FILES_SQL, ti.files_params("", "2026101506", ti.WSP_PRODUCTS)),
    ("FILES_SQL text ep202026 032", ti.FILES_SQL, ti.files_params("ep202026", "032", ti.TEXT_PRODUCTS)),
    ("MEMBERS_SQL ep202026 all", ti.MEMBERS_SQL, ti.members_params("ep202026", None)),
    ("MEMBERS_SQL ep202026 2026101112", ti.MEMBERS_SQL, ti.members_params("ep202026", "2026101112")),
    ("MEMBERS_SQL ep212026 all", ti.MEMBERS_SQL, ti.members_params("ep212026", None)),
]


def main():
    with lb.cluster(load_bank=False) as conn:
        cur = psycopg.ClientCursor(conn)
        out = []
        for name, sql, params in STATEMENTS:
            lit = cur.mogrify(sql, params)
            out.append({"name": name, "sql": " ".join(lit.split())})
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
