"""Bank the geo slice the river routes read, from Neon, READ-ONLY (lane d091770).

Run with the connection string in the environment, never in this file:

    NEON_DATABASE_URL=... python3 -I bank_from_neon.py

Every session is `default_transaction_read_only = on`, and the script READS THE FLAG
BACK (`SHOW transaction_read_only`) before it reads a row: a bank taken on a writable
session is refused. Nothing is written to Neon.

Each kind is banked as the exact `string_agg(row_to_json(t)::text, E'\\n' ORDER BY ...)`
text Neon returned, gzipped, and checked against NEON'S OWN sha256 of the same
expression. A hand edit to a bank file is then a red test (load_bank_d091770.text).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import pathlib

import psycopg

HERE = pathlib.Path(__file__).resolve().parent
BANK = HERE / "bank"
CUT = "2026-10-17T11:30:00+00:00"
# The window the values bank covers. Every dataset the three crosswalked rivers' and
# the Willamette's stations serve.
WIN_FROM, WIN_TO = "2026-10-16T00:00:00+00:00", "2026-10-17T00:00:00+00:00"
VALUE_DATASETS = ("cwms_outflow_hourly", "cwms_forebay_hourly", "cwms_spill_hourly",
                  "cwms_outflow_irregular", "cwms_forebay_irregular",
                  "usgs_iv_discharge", "usgs_iv_stage")

KINDS = {
    "walks": ("geo_layer_objects", "SELECT * FROM geo_layer_objects t", "t.object_id"),
    "display": ("geo_display_objects", "SELECT * FROM geo_display_objects t", "t.object_id"),
    "registry": ("geo_display_registry", "SELECT * FROM geo_display_registry t",
                 "t.registry_key"),
    "receipts": ("geo_unit_receipts",
                 f"SELECT * FROM geo_unit_receipts t WHERE t.measured_at < '{CUT}'",
                 "t.receipt_id"),
    "records": ("geo_records", f"SELECT * FROM geo_records t WHERE t.fetched_at < '{CUT}'",
                "t.record_id"),
    "crosswalk": ("geo_crosswalk", "SELECT * FROM geo_crosswalk t", "t.xw_id"),
    "values": ("timeseries_values",
               "SELECT * FROM timeseries_values t WHERE t.dataset = ANY(%(datasets)s) "
               "AND t.ts >= %(win_from)s AND t.ts < %(win_to)s",
               "t.dataset, t.series, t.ts"),
    "datasets": ("datasets",
                 "SELECT * FROM datasets t WHERE t.dataset_code = ANY(%(datasets)s)",
                 "t.dataset_code"),
    "sources": ("sources", "SELECT * FROM sources t WHERE t.source_code IN "
                           "(SELECT source_code FROM datasets WHERE dataset_code = "
                           "ANY(%(datasets)s))", "t.source_code"),
}
PARAMS = {"datasets": list(VALUE_DATASETS), "win_from": WIN_FROM, "win_to": WIN_TO}


def main() -> int:
    url = os.environ.get("NEON_DATABASE_URL")
    if not url:
        raise SystemExit("NEON_DATABASE_URL is not set")
    BANK.mkdir(parents=True, exist_ok=True)
    files = {}
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        ro = conn.execute("SHOW transaction_read_only").fetchone()[0]
        if ro != "on":
            raise SystemExit(f"refusing to bank: transaction_read_only is {ro!r}, not 'on'")
        print(f"transaction_read_only = {ro}")
        for kind, (table, inner, order) in KINDS.items():
            sql = (f"WITH s AS ({inner} ORDER BY {order}), "
                   f"t AS (SELECT string_agg(row_to_json(s)::text, E'\\n') AS txt FROM s) "
                   f"SELECT COALESCE(txt, '') AS txt, "
                   f"encode(sha256(convert_to(COALESCE(txt, ''), 'UTF8')), 'hex') AS sha256, "
                   f"(SELECT count(*) FROM s) AS n FROM t")
            txt, sha, n = conn.execute(sql, PARAMS).fetchone()
            local = hashlib.sha256(txt.encode()).hexdigest()
            if local != sha:
                raise SystemExit(f"{kind}: local sha256 {local} != Neon's {sha}")
            path = BANK / f"{kind}.jsonl.gz"
            path.write_bytes(gzip.compress(txt.encode(), mtime=0))
            files[kind] = {"path": f"bank/{kind}.jsonl.gz", "table": table,
                           "rows": n, "sha256": sha, "chars": len(txt)}
            print(f"  {kind:10s} {n:6d} rows  {len(txt):9d} chars  {sha[:16]}…")
        host = conn.execute("SELECT inet_server_addr()::text, current_database(), "
                            "version()").fetchone()
    manifest = {
        "lane": "d091770", "cut": CUT,
        "read": {"project_id": "fancy-block-96153928", "database": host[1],
                 "server_version": host[2].split(" on ")[0],
                 "transaction_read_only": "on",
                 "how": ("one autocommit session with default_transaction_read_only = on, "
                         "read back before any row was read; nothing was written")},
        "values_window": {"from": WIN_FROM, "to": WIN_TO,
                          "datasets": list(VALUE_DATASETS),
                          "note": ("nwm_reach_analysis_hourly and nwm_reach_forecast_hourly "
                                   "are NOT banked: no route reads them while the gate is "
                                   "not drawable, and the gate reads not drawable on 0 "
                                   "banked nwm_reach:* receipts")},
        "ddl": {
            "ddl_geo_tables.sql": ("energylake-pantry @ "
                                   "f2a75d16dcc0761f358b4ee8a33790e3d02256ac, "
                                   "migrations/302 lines 92-199 and 322 lines 114-148, "
                                   "verbatim"),
            "ddl_series_tables.sql": ("timeseries_values, datasets and sources from Neon's "
                                      "own live definition: they predate the pantry's "
                                      "numbered migrations"),
        },
        "pantry_d091752": {
            "gate_receipt.json": "d74ce5a3f2bee484d06483a7872179a17c31fad2ddf5242150c106b34fb37cc1",
            "gate_stdout.txt": "b2d828f2878d3c113ad1977e5c31d812d0004ba7f617a735e861870e96a511ef",
            "from": ("energylake-dashboard @ 3b2ba7f443e9253aa9c549c0fd881abb2f55d327, "
                     "src/components/hydro/fixtures/pantry_d091752/ (the shas d091763 §4 "
                     "states)"),
        },
        "files": files,
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest.json: {sum(f['rows'] for f in files.values())} rows in "
          f"{len(files)} banks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
