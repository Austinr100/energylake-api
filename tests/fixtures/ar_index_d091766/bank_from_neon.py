"""How bank/*.jsonl.gz were read off Neon for d091766 (the AR index), and how they are written.

ONE read-only transaction per read through the Neon connector (project Energylake,
production branch): `SET TRANSACTION READ ONLY`, `transaction_read_only` read back 'on' in
the same statement as the rows, then each table's rows as `row_to_json(t)::text`, joined by
'\\n' in primary-key order, with the sha-256 Neon computed over that same text. Nothing is
written; no row is constructed.

    python3 -I bank_from_neon.py --sql                        # the statements, as sent
    python3 -I bank_from_neon.py SMALL.json LANDFALL.json     # write bank/*.jsonl.gz, manifest.json

SMALL.json / LANDFALL.json are the connector's saved results (a JSON list: [] for the SET,
then one [{"kind", "read_at", "ro", "n", "sha256", "body"}] per statement). Each body is
checked against Neon's sha and row count before anything is written.

ar_scores is read for its count only: it held 0 rows at both reads (2026-10-17 12:49Z and
12:51Z), so there is nothing to bank, and A3's row is the one fixture the suite constructs
(scores_fixture.json), as the brief allows until the backtest's score mode writes rows.

Not run by the suite; the suite re-checks every sha on load.
"""
import gzip
import hashlib
import json
import pathlib
import sys

FIX = pathlib.Path(__file__).resolve().parent


def _agg(kind: str, table: str, order: str) -> str:
    return (f"SELECT '{kind}' AS kind, clock_timestamp() AS read_at, "
            f"current_setting('transaction_read_only') AS ro, count(*) AS n, "
            f"encode(sha256(convert_to(string_agg(row_to_json(t)::text, E'\\n' ORDER BY {order}), "
            f"'UTF8')), 'hex') AS sha256, string_agg(row_to_json(t)::text, E'\\n' ORDER BY {order}) "
            f"AS body FROM {table} t")


STATEMENTS = {
    "runs": _agg("runs", "ar_runs", "t.init_ts, t.level_set"),
    "display": _agg("display", "ar_display_objects", "t.level_set, t.kind, t.display_key"),
    "landfall": _agg("landfall", "ar_landfall_latest", "t.level_set, t.point_code, t.valid_ts"),
}
TABLES = {"runs": "ar_runs", "display": "ar_display_objects", "landfall": "ar_landfall_latest"}


def write(results: list) -> dict:
    out = {}
    for st in results:
        if not st or "body" not in st[0]:
            continue
        r = st[0]
        body = r["body"]
        got = hashlib.sha256(body.encode()).hexdigest()
        if got != r["sha256"] or len(body.split("\n")) != int(r["n"]) or r["ro"] != "on":
            raise SystemExit(f"{r['kind']}: sha {got} / rows / ro do not match Neon's")
        path = f"bank/{r['kind']}.jsonl.gz"
        (FIX / path).write_bytes(gzip.compress(body.encode(), 9, mtime=0))
        out[r["kind"]] = {"table": TABLES[r["kind"]], "path": path, "rows": int(r["n"]),
                          "sha256": r["sha256"], "read_at": r["read_at"], "ro": r["ro"]}
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["--sql"]:
        print("SET TRANSACTION READ ONLY;")
        for s in STATEMENTS.values():
            print(s + ";")
        raise SystemExit(0)
    bank = {}
    for p in sys.argv[1:]:
        bank.update(write(json.loads(pathlib.Path(p).read_text())))
    m = json.loads((FIX / "manifest.json").read_text()) if (FIX / "manifest.json").exists() else {}
    m["bank"] = {**m.get("bank", {}), **bank}
    (FIX / "manifest.json").write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
    print(json.dumps(bank, indent=1))
