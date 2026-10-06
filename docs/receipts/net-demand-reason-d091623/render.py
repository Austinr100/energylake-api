"""d091623: load_outlook.py's statements at this lane's NOW, as text.

    python docs/receipts/net-demand-reason-d091623/render.py <name> [arg] [--rows]

The statements and parameters are d091611's render.py (STATEMENTS, render,
lit) with NOW moved to the instant this bank was taken. --rows wraps the
statement so Postgres returns its rows as one JSON array of arrays in the
statement's column order (the fixture's compact form); `lines` and
`usability` stay objects. Every statement was run through the Neon
connector, read-only, and its answer written to
tests/fixtures/load_outlook_d091623/ by bank.py."""
import importlib.util
import pathlib
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
NOW = datetime(2026, 10, 6, 12, 40, tzinfo=timezone.utc)

_spec = importlib.util.spec_from_file_location(
    "render_d091611", HERE.parent / "load-net-demand-api-d091611" / "render.py")
r = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r)
r.NOW = NOW
r.W.clear()
r.W.update(r.lo.windows(NOW))

COLUMNS = {
    "fcst": ["product", "ts", "value", "publish_time"],
    "series": ["ts", "value"],
    "hub": ["series", "ts", "value"],
    "truth": ["part", "ts", "value"],
    "gen": ["init_ts", "target_ts", "lead_h", "lead_band", "registry_mw",
            "calibrated_mw", "calibration_id"],
}
KIND = {"fcst": "fcst", "df": "series", "native": "series", "eia_d": "series",
        "hub": "hub", "truth": "truth", "gen_rows": "gen", "bt_rows": "gen",
        "gen_newest": None, "usability": None, "lines": None}


def statement(name: str, arg: str = "", rows: bool = False) -> str:
    sql, params = r.STATEMENTS[name](arg)
    sql = r.render(sql, params)
    if rows:
        cols = COLUMNS.get(KIND[name]) if KIND[name] else None
        if cols:
            arr = ", ".join(f"x.{c}" for c in cols)
            sql = (f"SELECT coalesce(json_agg(json_build_array({arr})), '[]'::json)::text AS rows "
                   f"FROM ({sql}) x")
        else:
            sql = f"SELECT coalesce(json_agg(x), '[]'::json)::text AS rows FROM ({sql}) x"
    return sql


if __name__ == "__main__":
    name, arg = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "")
    print(statement(name, arg, rows="--rows" in sys.argv))
