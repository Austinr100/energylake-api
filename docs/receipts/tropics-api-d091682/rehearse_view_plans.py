"""d091682 STOP-V, rehearsed locally: every Tropics statement three ways on a
throwaway Postgres 16 holding the d091673 bank with migration 294 applied over
it (as on Neon), plus one constructed correction so "in force" is not trivial:

  before   d091673's statements on the bare tables (git c40c231:tropics.py)
  after    this lane's draft statements on 294's views (draft_rows_in_force.patch)
  fixed    the same statements after proposed_pantry_view_fix.sql

at the bank's size and at 31x (the bank and 30 copies, as d091673's T10). For
each: the plan's scans of the big tables, shared buffers, and the median of 5
executions. Then the rows each `after` statement returns are compared with
`fixed`'s: the fix must change no row. Writes view_plans_local.json and prints
a table. Nothing touches Neon.

    python3 docs/receipts/tropics-api-d091682/rehearse_view_plans.py
"""
import json
import pathlib
import statistics
import sys
from datetime import timedelta

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(HERE)]

import load_bank_d091673 as lb  # noqa: E402
from render_explains import BASE, draft, module_at, statements  # noqa: E402

DDL_294 = (ROOT / "tests/fixtures/tropics_d091682/ddl_294.sql").read_text()
FIX = (HERE / "proposed_pantry_view_fix.sql").read_text()
BIG = ("tropical_track_points", "tropical_place_odds", "tropical_file_vintage")

# Simon's advisory 010 corrected: tau 24 moved 0.3 deg, tau 120 dropped; and
# Simon's newest PWS issuance corrected: one value changed, one place dropped.
CORRECT = """
INSERT INTO tropical_track_points (storm_id, source, init_ts, advisory, tau, valid_ts, lat, lon,
                                   vmax_kt, mslp_hpa, radii, stage, source_r2_key, revision)
SELECT storm_id, source, init_ts, advisory, tau, valid_ts,
       lat + CASE WHEN tau = 24 THEN 0.3 ELSE 0 END, lon, vmax_kt, mslp_hpa, radii, stage,
       source_r2_key, 1
  FROM tropical_track_points
 WHERE storm_id = 'ep202026' AND source = 'nhc_official' AND advisory = '010'
   AND tau <> (SELECT max(tau) FROM tropical_track_points
                WHERE storm_id = 'ep202026' AND source = 'nhc_official' AND advisory = '010');
WITH iss AS (SELECT max(issued_ts) AS ts FROM tropical_place_odds
              WHERE storm_id = 'ep202026' AND source = 'nhc_pws'),
     pick AS (SELECT min(odds_id) FILTER (WHERE NOT below_1pct AND value < 99) AS changed_id,
                     max(odds_id) AS last_id
                FROM tropical_place_odds, iss
               WHERE storm_id = 'ep202026' AND source = 'nhc_pws' AND issued_ts = iss.ts)
INSERT INTO tropical_place_odds (storm_id, issued_ts, source, advisory, place_id, threshold_kt,
                                 radius_km, window_h, kind, value, below_1pct, n_members, scored,
                                 source_r2_key, revision)
SELECT o.storm_id, o.issued_ts, o.source, o.advisory, o.place_id, o.threshold_kt, o.radius_km,
       o.window_h, o.kind, CASE WHEN o.odds_id = pick.changed_id THEN o.value + 1 ELSE o.value END,
       o.below_1pct, o.n_members, o.scored, o.source_r2_key, 1
  FROM tropical_place_odds AS o, iss, pick
 WHERE o.storm_id = 'ep202026' AND o.source = 'nhc_pws' AND o.issued_ts = iss.ts
   AND o.place_id <> (SELECT place_id FROM tropical_place_odds WHERE odds_id = pick.last_id);
"""


def scale(conn, copies=30):
    for k in range(1, copies + 1):
        season = 1995 + k
        p = {"s": str(season), "d": timedelta(days=364 * k)}
        conn.execute(
            "INSERT INTO tropical_storms SELECT substr(storm_id, 1, 4) || %(s)s, basin, number, "
            "%(s)s::smallint, 'inactive', names, aliases, first_seen - %(d)s, last_seen - %(d)s, "
            "last_lat, last_lon, created_ts, updated_ts FROM tropical_storms WHERE season = 2026", p)
        conn.execute(
            "INSERT INTO tropical_track_points (storm_id, source, init_ts, advisory, tau, valid_ts, "
            "lat, lon, vmax_kt, mslp_hpa, radii, stage, revision) SELECT substr(storm_id, 1, 4) || %(s)s, "
            "source, init_ts - %(d)s, advisory, tau, valid_ts - %(d)s, lat, lon, vmax_kt, mslp_hpa, "
            "radii, stage, revision FROM tropical_track_points WHERE storm_id LIKE '%%2026'", p)
        conn.execute(
            "INSERT INTO tropical_place_odds (storm_id, issued_ts, source, advisory, place_id, "
            "threshold_kt, radius_km, window_h, kind, value, below_1pct, n_members, scored, revision) "
            "SELECT substr(storm_id, 1, 4) || %(s)s, issued_ts - %(d)s, source, advisory, place_id, "
            "threshold_kt, radius_km, window_h, kind, value, below_1pct, n_members, scored, revision "
            "FROM tropical_place_odds WHERE storm_id LIKE '%%2026'", p)
    conn.execute("ANALYZE")


def scans(plan):
    out = []

    def walk(n):
        if n.get("Relation Name") in BIG:
            out.append(f'{n["Node Type"]} {n["Relation Name"]}'
                       + (f' ({n["Index Name"]})' if n.get("Index Name") else ""))
        for c in n.get("Plans", []):
            walk(c)
    walk(plan)
    return out


def measure(conn, sql, params, runs=5):
    plans = [conn.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params).fetchone()
             ["QUERY PLAN"][0] for _ in range(runs + 1)][1:]          # the first run warms
    top = plans[-1]["Plan"]
    return {"ms": round(statistics.median(p["Execution Time"] for p in plans), 3),
            "buffers": top.get("Shared Hit Blocks", 0) + top.get("Shared Read Blocks", 0),
            "seq_scan_big": sorted({s for s in scans(top) if s.startswith("Seq Scan")}),
            "scans": scans(top)}


def rows(conn, sql, params):
    return [json.dumps(r, default=str, sort_keys=True) for r in conn.execute(sql, params).fetchall()]


def run(conn, label):
    before_mod, after_mod = module_at(BASE), draft()
    out = {}
    for (name, sql_b, p), (_n, sql_a, _p) in zip(statements(before_mod), statements(after_mod)):
        out[name] = {"before": measure(conn, sql_b, p), "after": measure(conn, sql_a, p)}
        out[name]["_rows_after"] = rows(conn, sql_a, p)
    with conn.transaction(force_rollback=True):
        conn.execute(FIX)
        conn.execute("ANALYZE tropical_track_points, tropical_place_odds")
        for name, sql_a, p in statements(after_mod):
            out[name]["fixed"] = measure(conn, sql_a, p)
            assert rows(conn, sql_a, p) == out[name].pop("_rows_after"), (label, name)
            out[name]["fixed_rows_equal_after"] = True
    return out


def main():
    res = {}
    with lb.cluster() as conn:
        conn.execute(DDL_294)
        conn.execute(CORRECT)
        n1 = conn.execute("SELECT count(*) AS n FROM tropical_track_points WHERE revision > 0").fetchone()["n"]
        n2 = conn.execute("SELECT count(*) AS n FROM tropical_place_odds WHERE revision > 0").fetchone()["n"]
        assert n1 > 0 and n2 > 0
        conn.execute("ANALYZE")
        res["bank"] = run(conn, "bank")
        scale(conn)
        res["x31"] = run(conn, "x31")
        res["sizes"] = {t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                        for t in ("tropical_track_points", "tropical_place_odds")}
        res["constructed_revision_rows"] = {"track_points": n1, "place_odds": n2}
    (HERE / "view_plans_local.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    for size in ("bank", "x31"):
        print(f"\n== {size}")
        for name, m in res[size].items():
            print(f"{name:22s}" + "  ".join(
                f"{k} {m[k]['ms']:7.3f} ms {m[k]['buffers']:5d} buf "
                f"{'SEQ ' + ','.join(s.split()[2] for s in m[k]['seq_scan_big']) if m[k]['seq_scan_big'] else 'idx':>30s}"
                for k in ("before", "after", "fixed")))


if __name__ == "__main__":
    main()
