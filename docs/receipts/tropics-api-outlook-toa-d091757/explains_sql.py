"""d091757: every NEW ledger read the routes make, with real parameters, as literal SQL.

    python3 -I docs/receipts/tropics-api-outlook-toa-d091757/explains_sql.py > docs/receipts/tropics-api-outlook-toa-d091757/explains.json

psycopg's own ClientCursor.mogrify over the routes' statements (tropics_index.py) and
parameter builders, on a throwaway local cluster (only to mogrify). Each was then run on
Neon as EXPLAIN (ANALYZE, BUFFERS) through the Neon connector, in one read-only
transaction: neon_plans.txt. Not run by the suite (test X1 reads both).

The new reads: OUTLOOK_STAMP_SQL (the graphics' newest stamp; a text's newest stamp at or
before the graphics', at the newest issuance and at an old one), DISPLAY_FILES_SQL on the
outlook (graphics and text, storm_id '') and DISPLAY_FILES_SQL / FILES_SQL on /products
with the TOA pairs added (their product lists changed, so their plans are re-taken).
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import psycopg  # noqa: E402

import tropics_index as ti  # noqa: E402
import load_bank_d091673 as lb  # noqa: E402

GRAPHICS = [(n, None) for n in ti.OUTLOOK_GRAPHICS]
PRODUCTS = ti.ADVISORY_PRODUCTS + ti.TOA_PRODUCTS

STATEMENTS = [
    ("OUTLOOK_STAMP_SQL two_areas newest", ti.OUTLOOK_STAMP_SQL, ti.outlook_stamp_params("two_areas")),
    ("OUTLOOK_STAMP_SQL two_text_at <= 202610152347", ti.OUTLOOK_STAMP_SQL,
     ti.outlook_stamp_params("two_text_at", "202610152347")),
    ("OUTLOOK_STAMP_SQL two_text_ep <= 202610140517", ti.OUTLOOK_STAMP_SQL,
     ti.outlook_stamp_params("two_text_ep", "202610140517")),
    ("DISPLAY_FILES_SQL outlook graphics 202610152347", ti.DISPLAY_FILES_SQL,
     ti.display_files_params("", "202610152347", GRAPHICS)),
    ("DISPLAY_FILES_SQL outlook text_at 202610152319", ti.DISPLAY_FILES_SQL,
     ti.display_files_params("", "202610152319", [("two_text_at", None)])),
    ("DISPLAY_FILES_SQL products+toa ep182026 054", ti.DISPLAY_FILES_SQL,
     ti.display_files_params("ep182026", "054", PRODUCTS)),
    ("FILES_SQL products+toa ep182026 054", ti.FILES_SQL, ti.files_params("ep182026", "054", PRODUCTS)),
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
