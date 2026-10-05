"""Render load_outlook.py's statements with production parameters, as text.

    python docs/receipts/load-net-demand-api-d091611/render.py <name>

prints one statement (the route's own text, parameters substituted as SQL
literals) for the Neon connector: the bank (bank.json) and the plans
(plans.md) were made from exactly these. NOW is the instant the bank was
taken; windows() derives every bound from it."""
import json, pathlib, sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import load_outlook as lo

NOW = datetime(2026, 10, 5, 3, 30, tzinfo=timezone.utc)
W = lo.windows(NOW)


def lit(v):
    if isinstance(v, datetime):
        return f"'{v.isoformat()}'::timestamptz"
    if isinstance(v, (list, tuple)):
        if v and isinstance(v[0], datetime):
            return "ARRAY[" + ",".join(lit(x) for x in v) + "]::timestamptz[]"
        if v and isinstance(v[0], int):
            return "ARRAY[" + ",".join(str(x) for x in v) + "]::bigint[]"
        return "ARRAY[" + ",".join("'" + str(x).replace("'", "''") + "'" for x in v) + "]::text[]"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def render(sql, params):
    for k, v in sorted(params.items(), key=lambda kv: -len(kv[0])):
        sql = sql.replace(f"%({k})s", lit(v))
    return sql


def gen_key(part):
    tech, model = lo.GEN[part]
    return {"tech": tech, "area": "CISO", "model": model}


STATEMENTS = {
    "fcst": lambda area: (lo.FCST_SQL, {"area": area, "lo": W["score_lo"], "hi": W["outlook_hi"]}),
    "df": lambda code: (lo.SERIES_SQL, {"dataset": lo.DF_DATASET, "series": code,
                                         "lo": W["score_lo"], "hi": W["outlook_hi"]}),
    "native": lambda _x: (lo.SERIES_SQL, {"dataset": lo.NATIVE[0], "series": lo.NATIVE[1],
                                           "lo": W["score_lo"], "hi": NOW}),
    "eia_d": lambda code: (lo.SERIES_SQL, {"dataset": lo.D_DATASET, "series": code,
                                            "lo": W["score_lo"], "hi": NOW}),
    "usability": lambda areas: (lo.USABILITY_SQL, {
        "areas": areas.split(",") if areas else [c for c, _e in lo.BA_ACTUALS],
        "lo": W["score_lo"], "hi": W["score_hi"],
        "dropout": lo.USABILITY_RULE["dropout_fraction_of_median"]}),
    "hub": lambda _x: (lo.HUB_DAM_SQL, {"lo": W["score_lo"], "hi": W["outlook_hi"]}),
    "truth": lambda _x: (lo.TRUTH_SQL, {"lo": W["score_lo"], "hi": NOW}),
    "gen_newest": lambda part: (lo.GEN_NEWEST_SQL, gen_key(part)),
    "gen_rows": lambda arg: (lo.GEN_ROWS_SQL, {
        **gen_key(arg.split("@")[0]), "inits": [datetime.fromisoformat(arg.split("@")[1])],
        "lo": W["today_lo"], "hi": W["outlook_hi"]}),
    "bt_rows": lambda part: (lo.GEN_ROWS_SQL, {
        **gen_key(part), "inits": lo.backtest_inits(W["score_days"]),
        "lo": W["score_lo"], "hi": W["score_hi"]}),
    "lines": lambda ids: (lo.LINES_SQL, {"ids": [int(x) for x in ids.split(",")]}),
}

if __name__ == "__main__":
    name, arg = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
    sql, params = STATEMENTS[name](arg)
    sql = render(sql, params)
    if "--json" in sys.argv:
        sql = f"SELECT coalesce(json_agg(x), '[]'::json) AS rows FROM ({sql}) x"
    if "--explain" in sys.argv:
        sql = "EXPLAIN (ANALYZE, BUFFERS) " + sql
    print(sql)
