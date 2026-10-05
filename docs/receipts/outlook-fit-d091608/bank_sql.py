"""The banking statement: for each calibrated area of one tech, the route's
own statements (solar_outlook / wind_outlook as merged on this branch), with
the parameters written in as literals, wrapped in one json_build_object per
area so one read-only SELECT through the Neon connector returns the whole
bank. Wind also carries `lines_derived`: the lines as the route on main
derived them (CALIBRATION_SQL at main, the LATERAL), for A3 and Step 1.

    python docs/receipts/outlook-fit-d091608/bank_sql.py solar|wind > bank_<tech>.sql
"""
import pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import solar_outlook as so
import wind_outlook as wo

AREAS = {
    "solar": [("hub", "NP15"), ("hub", "ZP26"), ("hub", "SP15"), ("hub_sum", "HUBSUM"), ("ba", "CISO")],
    "wind": [("hub", "NP15"), ("hub", "ZP26"), ("hub", "SP15"), ("hub_sum", "HUBSUM"), ("ba", "CISO")],
}
MAIN_WIND_CAL = None


def main_wind_calibration_sql() -> str:
    """CALIBRATION_SQL as main served it (the derivation), read from git."""
    src = subprocess.run(["git", "-C", str(ROOT), "show", "main:wind_outlook.py"],
                         capture_output=True, text=True, check=True).stdout
    ns: dict = {}
    head = src.split("def parse_model")[0]
    exec(compile(head.replace("import solar_outlook as so", "import solar_outlook as so"),
                 "main_wind", "exec"), ns)
    body = src.split("SCORES_SQL = {kind")[1]
    seg = body[body.index("_PT_DAY_LO"):body.index("def actual_pairs")]
    exec(seg, ns)
    return ns["CALIBRATION_SQL"]


def lit(sql: str, params: dict) -> str:
    for k, v in params.items():
        sql = sql.replace(f"%({k})s", v)
    assert "%(" not in sql, re.findall(r"%\(\w+\)s", sql)
    return sql


def q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def area_ctes(tech: str, n: int, kind: str, area: str) -> tuple[list, str]:
    """(CTEs, the area's json_build_object). Each route statement appears
    once; the issuance's init and prev_init, and the line ids, are read from
    the CTEs before them, as the route reads them from the rows before."""
    m = so if tech == "solar" else wo
    key = {"tech": q(m.TECH), "area_kind": q(kind), "area": q(area), "model": q(m.DEFAULT_MODEL)}
    i, h = f"i{n}", f"h{n}"
    ctes = [f"{i} AS ({lit(m.ISSUANCE_NEWEST_SQL, key)})",
            f"{h} AS ({lit(m.HOURS_SQL, {**key, 'init': f'(SELECT init_ts FROM {i})', 'prev_init': f'(SELECT prev_init_ts FROM {i})'})})"]
    mv = q("solar_pv_v1" if tech == "solar" else "wind_v1")
    ids = (f"ARRAY(SELECT DISTINCT x FROM (SELECT unnest(ARRAY[calibration_id, prev_calibration_id]) x "
           f"FROM {h}) z WHERE x IS NOT NULL)")
    parts = [f"'issuance', (SELECT row_to_json(x) FROM {i} x)",
             f"'hours', (SELECT json_agg(x) FROM {h} x)",
             f"'scores', (SELECT json_agg(x) FROM ({lit(m.SCORES_SQL[kind], {**key, 'method_version': mv})}) x)",
             f"'lines', (SELECT json_agg(x) FROM ({lit(m.CALIBRATION_SQL, {**key, 'ids': ids})}) x)"]
    if tech == "wind":
        parts.append(f"'lines_derived', (SELECT json_agg(x) FROM ({lit(main_wind_calibration_sql(), {**key, 'ids': ids})}) x)")
    acts = m.ACTUALS_SQL.get((kind, area))
    if acts is not None:
        parts.append(f"'actuals', (SELECT json_agg(x) FROM ({lit(acts, {'lo': f'(SELECT min(target_ts) FROM {h})', 'hi': f'(SELECT max(target_ts) FROM {h})'})}) x)")
    if tech == "solar":
        parts.append(f"'fleet', (SELECT json_agg(x) FROM ({lit(so.FLEET_SQL[kind], {'tech': key['tech'], 'area': key['area']})}) x)")
    return ctes, f"{q(area)}, json_build_object({', '.join(parts)})"


if __name__ == "__main__":
    tech = sys.argv[1]
    ctes, objs = [], []
    for n, (k, a) in enumerate(AREAS[tech]):
        c, o = area_ctes(tech, n, k, a)
        ctes += c
        objs.append(o)
    sql = f"WITH {', '.join(ctes)} SELECT json_build_object({', '.join(objs)}) AS bank"
    print(" ".join(sql.split()))
