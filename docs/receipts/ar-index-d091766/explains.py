"""The statements X1's Neon receipts are of: each route read with its parameters bound as the
route binds them, as sent to Neon (one read-only transaction, EXPLAIN (ANALYZE, BUFFERS)).

    python3 -I docs/receipts/ar-index-d091766/explains.py > docs/receipts/ar-index-d091766/explains.json
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
import ar_index as ar  # noqa: E402


def bind(sql: str, params: dict) -> str:
    for k, v in params.items():
        sql = sql.replace(f"%({k})s", f"'{v}'")
    return " ".join(sql.split())


STATEMENTS = [
    ("INDEX_SQL", bind(ar.INDEX_SQL, ar.params())),
    ("REGISTRY_SQL", bind(ar.REGISTRY_SQL, ar.params())),
    ("SCORES_SQL", bind(ar.SCORES_SQL, ar.params())),
    ("LANDFALL_INIT_SQL", bind(ar.LANDFALL_INIT_SQL, ar.params())),
    ("LANDFALL_SQL", bind(ar.LANDFALL_SQL, ar.params(init_ts="2026-10-17T00:00:00+00:00"))),
]

if __name__ == "__main__":
    print(json.dumps([{"name": n, "sql": s} for n, s in STATEMENTS], indent=1))
