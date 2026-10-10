"""Write explains.sql: each CURVES/PLACES read d091679 planned, twice, on the
same bank in one sitting: B* is main's statement (d091679's pinned_sql.json),
A* is this lane's (cpc_outlooks.py), with the params as literals. Run from the
repo root:  python docs/receipts/cpc-api-method-pin-d091691/make_explains.py"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT)]
HERE = pathlib.Path(__file__).resolve().parent
import cpc_outlooks as co  # noqa: E402

MAIN = json.loads((ROOT / "docs/receipts/cpc-outlooks-d091679/pinned_sql.json").read_text())
READS = [
    ("01", "CURVES_SQL", {"product": "610temp", "place_kind": "station", "place": "USW00023188", "weighting": "", "n": 1}),
    ("02", "CURVES_SQL", {"product": "814temp", "place_kind": "station", "place": "USW00023188", "weighting": "", "n": 1}),
    ("03", "CURVES_SQL", {"product": "814temp", "place_kind": "region", "place": "pnw", "weighting": "population", "n": 14}),
    ("04", "CURVES_SQL", {"product": "610temp", "place_kind": "station", "place": "USW00003017", "weighting": "", "n": 14}),
    ("05", "PLACES_SQL", {}),
    ("06", "PLACES_NEWEST_SQL", {"products": ["610temp", "814temp"]}),
]
D091679_TAG = {"01": "C01", "02": "C02", "03": "C03", "04": "C04", "05": "P01", "06": "P02"}


def flat(s):
    return " ".join(s.split())


def lit(v):
    if isinstance(v, list):
        return "ARRAY[" + ",".join(f"'{x}'" for x in v) + "]"
    return str(v) if isinstance(v, int) else f"'{v}'"


def stmt(sql, p):
    return "EXPLAIN (ANALYZE, BUFFERS) " + re.sub(r"%\((\w+)\)s", lambda m: lit(p[m.group(1)]), flat(sql))


if __name__ == "__main__":
    out = ["-- d091691 plan receipts. B<nn> = main's statement (d091679's pin), A<nn> = this",
           "-- lane's pinned statement, params as literals; same bank, same sitting, Neon",
           "-- production read-only. <nn> is d091679's C01..C04, P01, P02 in order.", ""]
    for kind, src in (("B", lambda n: MAIN[n]), ("A", lambda n: getattr(co, n))):
        for nn, name, p in READS:
            out.append(f"-- {kind}{nn} {name} {json.dumps(p)}")
            out.append(stmt(src(name), p) + ";")
            out.append("")
    (HERE / "explains.sql").write_text("\n".join(out))
    print("written", len(READS) * 2)
