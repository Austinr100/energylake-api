"""Bank the five routes' BODIES off the banked cluster — the page lane's vectors.

    cd tests && python3 -I ../docs/receipts/hydro-rivers-api-d091770/bank_bodies.py

Each body is built by the route's own builder over the sha-checked bank
(load_bank_d091770), serialised with sorted keys, and written beside its sha256.
These are what the dashboard lane pins against, so a body shape that moves here is
a red test there rather than a surprise on the page.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent.parent.parent / "tests"), str(HERE.parent.parent.parent)]

import load_bank_d091770 as lb          # noqa: E402
import hydro_rivers as hr               # noqa: E402

COLUMBIA = "nhdplus_hr_columbia_gcl_bon"
WILLAMETTE = "nhdplus_hr_willamette_hcr_columbia"


def read(conn, sql, params=None):
    return conn.execute(sql, params).fetchall()


def main() -> int:
    out = HERE / "bodies"
    out.mkdir(exist_ok=True)
    index_ = {}
    with lb.cluster() as conn:
        walks = read(conn, hr.WALKS_SQL)
        records = read(conn, hr.RECORDS_SQL)
        receipts = read(conn, hr.RECEIPTS_SQL)
        registry = read(conn, hr.REGISTRY_SQL,
                        {"schema": hr.RIVERS_REGISTRY_SCHEMA})[0]

        bodies = {"index": hr.build_index(walks, registry, records)}
        for name, walk in (("columbia", COLUMBIA), ("willamette", WILLAMETTE)):
            bodies[f"stations_{name}"] = hr.build_stations(name, walk, walks, records)

            recs = hr.shape_ledger(walks, records)["records"].get(walk, [])
            dams = [r for r in recs if r["kind"] == "cwms_project"]
            pairs = [("cwms_outflow_hourly", r["source_key"]) for r in dams]
            present = set()
            if pairs:
                present = {(r["dataset"], r["series"]) for r in read(conn, f"""
                    SELECT d.dataset, d.series
                    FROM (VALUES {hr.values_sql(len(pairs))}) AS d(dataset, series)
                    CROSS JOIN LATERAL (SELECT 1 FROM timeseries_values t
                        WHERE t.dataset = d.dataset AND t.series = d.series
                        ORDER BY t.ts DESC LIMIT 1) AS v""",
                    [x for pair in pairs for x in pair])}
            gate = hr.build_gate(name, walk, walks, records, receipts, present)
            bodies[f"gate_{name}"] = gate

            stations = [{"source_key": r["source_key"],
                         "serves": hr.station_series(r)[0]} for r in recs]
            flow_pairs = [(ds, k) for ds, k, _ in hr.flow_pairs(stations)]
            values = []
            if flow_pairs:
                limit = max(hr.window_limit(ds, lb.WIN_FROM, lb.WIN_TO)
                            for ds, _ in flow_pairs)
                values = read(conn, hr.series_window_sql(len(flow_pairs)),
                              [x for pair in flow_pairs for x in pair]
                              + [lb.WIN_FROM, lb.WIN_TO, limit])
            bodies[f"flow_{name}"] = hr.build_flow(
                name, walk, walks, records, receipts, values, gate,
                start=lb.WIN_FROM, end=lb.WIN_TO)
            bodies[f"reaches_{name}"] = hr.build_reaches(
                name, walk, walks, records, gate, [],
                start=lb.WIN_FROM, end=lb.WIN_TO)

    for name, body in sorted(bodies.items()):
        text = json.dumps(body, indent=2, sort_keys=True, default=str) + "\n"
        (out / f"{name}.json").write_text(text)
        index_[name] = {"bytes": len(text.encode()),
                        "sha256": hashlib.sha256(text.encode()).hexdigest()}
        print(f"  {name:22s} {len(text.encode()):7d} B  "
              f"{index_[name]['sha256'][:16]}…")
    (out / "SHA256SUMS.json").write_text(
        json.dumps({"lane": "d091770", "cut": lb.MANIFEST["cut"],
                    "built_from": "tests/fixtures/hydro_rivers_d091770 (sha-checked)",
                    "serialisation": "json.dumps(indent=2, sort_keys=True, default=str)",
                    "bodies": index_}, indent=2, sort_keys=True) + "\n")
    print(f"{len(index_)} bodies banked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
