"""The two site reads at FULL size, on a local Postgres 16 (production's
implied_gen_sites and implied_gen_site_latest are empty on 2026-10-03, so their
production plans say nothing about scale). Tables are migration 264's DDL;
rows are synthetic at the writer's measured sizes: 1,914 units at 1,645 plants,
240 target hours per plant (one cycle, f001..f240).

    python docs/receipts/solar-outlook-api-d091568/plans_local_scale.py
"""
import os, re, shutil, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
import psycopg
import solar_outlook as so

BIN = "/usr/lib/postgresql/16/bin"
DDL = re.search(r"(CREATE TABLE implied_gen_sites .*?\);\n).*?(CREATE TABLE implied_gen_site_latest .*?\);\n)",
                open("/home/user/energylake-pantry/migrations/264_implied_generation_solar.sql").read(), re.S)

def lit(sql, p):
    return re.sub(r"%\((\w+)\)s", lambda m: f"'{p[m.group(1)]}'", sql)

def main():
    d = tempfile.mkdtemp(prefix="pg_solar_plan_"); shutil.chown(d, "postgres")
    pg = ["runuser", "-u", "postgres", "--"]
    subprocess.run(pg + [f"{BIN}/initdb", "-D", f"{d}/data", "-A", "trust", "-U", "postgres"], check=True, capture_output=True)
    subprocess.run(pg + [f"{BIN}/pg_ctl", "-D", f"{d}/data", "-w", "-l", f"{d}/log", "-o", f"-k {d} -c listen_addresses=''", "start"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        c = psycopg.connect(f"host={d} user=postgres", autocommit=True)
        c.execute(DDL.group(1)); c.execute(DDL.group(2))
        c.execute("""INSERT INTO implied_gen_sites (plant_code, generator_id, plant_name, ac_mw, dc_mw, dc_basis,
                       mount, mount_basis, hub, ba_code, state, latitude, longitude, method_version)
                     SELECT 1 + (g % 1645), 'G' || g, 'P' || (g % 1645), 25, 31.5, 'stated', 'single_axis', 'stated',
                            (ARRAY['NP15','ZP26','SP15',NULL])[1 + g % 4], 'CISO', 'CA', 35, -118, 'solar_pv_v1'
                       FROM generate_series(0, 1913) g""")
        init = datetime(2026, 10, 3, 6, tzinfo=timezone.utc)
        c.execute("""INSERT INTO implied_gen_site_latest
                     SELECT 'solar_pv', p, 'gfs', %s + make_interval(hours => h - 1), %s, h, 10, 0, 'solar_pv_v1'
                       FROM generate_series(1, 1645) p, generate_series(1, 240) h""", (init, init))
        c.execute("VACUUM ANALYZE implied_gen_sites"); c.execute("VACUUM ANALYZE implied_gen_site_latest")
        n = c.execute("SELECT count(*), pg_size_pretty(pg_total_relation_size('implied_gen_site_latest')) FROM implied_gen_site_latest").fetchone()
        print(f"# implied_gen_site_latest: {n[0]:,} rows, {n[1]}; implied_gen_sites: 1,914 rows\n")
        lo = datetime(2026, 10, 3, 7, tzinfo=timezone.utc)
        for name, sql, p in [
            ("SITE_LATEST_SQL, a Pacific day", so.SITE_LATEST_SQL, {"tech": "solar_pv", "model": "gfs", "lo": lo, "hi": lo + timedelta(hours=24)}),
            ("SITE_LATEST_SQL, one hour", so.SITE_LATEST_SQL, {"tech": "solar_pv", "model": "gfs", "lo": lo, "hi": lo + timedelta(hours=1)}),
            ("SITE_UNITS_SQL", so.SITE_UNITS_SQL, {"tech": "solar_pv"}),
            ("FLEET_SQL[hub_sum]", so.FLEET_SQL["hub_sum"], {"tech": "solar_pv"}),
        ]:
            for _ in range(2):          # second run is the warm one printed
                plan = c.execute("EXPLAIN (ANALYZE, BUFFERS) " + lit(sql, p)).fetchall()
            print(f"## {name}\n```\n" + "\n".join(r[0] for r in plan) + "\n```\n")
    finally:
        subprocess.run(pg + [f"{BIN}/pg_ctl", "-D", f"{d}/data", "-m", "immediate", "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    main()
