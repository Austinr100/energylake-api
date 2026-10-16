"""d091746: every ledger read the index routes make now, with real parameters, as literal SQL.

    python3 -I docs/receipts/tropics-api-revision-read-d091746/explains_sql.py > docs/receipts/tropics-api-revision-read-d091746/explains.json

psycopg's own ClientCursor.mogrify over the routes' statements (tropics_index.py)
and parameter builders, on a throwaway local cluster (only to mogrify). Each was
then run on Neon as EXPLAIN (ANALYZE, BUFFERS) through the Neon connector, in a
read-only transaction: neon_plans.txt. Not run by the suite (test X1 reads both).
The new reads (DISPLAY_FILES_SQL, DISPLAY_MEMBERS_SQL) are taken on the
identities the I2/T2/I3 vectors name; the NHC and parquet reads are d091741's,
re-taken because their text changed (FILES_SQL and MEMBERS_SQL no longer name the
display source).
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
    ("STORM_SQL ep182026", tr.STORM_SQL, {"storm_id": "ep182026"}),
    ("ENTRY_NEWEST_SQL ep202026", ti.ENTRY_NEWEST_SQL, {"storm_id": "ep202026"}),
    ("ADVISORY_AT_SQL ep182026 054", ti.ADVISORY_AT_SQL, {"storm_id": "ep182026", "advisory": "054"}),
    ("ENTRY_SQL ep182026 054", ti.ENTRY_SQL, ti.entry_params("ep182026", "054")),
    ("FILES_SQL products ep182026 054", ti.FILES_SQL,
     ti.files_params("ep182026", "054", ti.ADVISORY_PRODUCTS)),
    ("DISPLAY_FILES_SQL products ep182026 054", ti.DISPLAY_FILES_SQL,
     ti.display_files_params("ep182026", "054", ti.ADVISORY_PRODUCTS)),
    ("FILES_SQL wsp 2026101012", ti.FILES_SQL, ti.files_params("", "2026101012", ti.WSP_PRODUCTS)),
    ("DISPLAY_FILES_SQL wsp 2026101012", ti.DISPLAY_FILES_SQL,
     ti.display_files_params("", "2026101012", ti.WSP_PRODUCTS)),
    ("DISPLAY_FILES_SQL wsp 2026101512", ti.DISPLAY_FILES_SQL,
     ti.display_files_params("", "2026101512", ti.WSP_PRODUCTS)),
    ("FILES_SQL text al092026 016", ti.FILES_SQL, ti.files_params("al092026", "016", ti.TEXT_PRODUCTS)),
    ("DISPLAY_FILES_SQL text al092026 016", ti.DISPLAY_FILES_SQL,
     ti.display_files_params("al092026", "016", ti.TEXT_PRODUCTS)),
    ("DISPLAY_FILES_SQL text ep202026 033", ti.DISPLAY_FILES_SQL,
     ti.display_files_params("ep202026", "033", ti.TEXT_PRODUCTS)),
    ("MEMBERS_SQL ep202026 all", ti.MEMBERS_SQL, ti.members_params("ep202026", None)),
    ("DISPLAY_MEMBERS_SQL ep202026 all", ti.DISPLAY_MEMBERS_SQL, ti.display_members_params("ep202026", None)),
    ("DISPLAY_MEMBERS_SQL ep202026 2026101112", ti.DISPLAY_MEMBERS_SQL,
     ti.display_members_params("ep202026", "2026101112")),
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
