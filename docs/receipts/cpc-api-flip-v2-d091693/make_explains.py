"""Write explains.sql: each CPC read that names cpc_outlook_curves,
cpc_curve_verdicts or v_cpc_curves_drawable, with d091679's params (its C01-C04,
P01, P02, the same ones d091691 re-took), twice: Axx is the v2-pinned SQL in
cpc_outlooks.py (this lane), Bxx is main a10b6f2's v1-pinned SQL (d091691's
pinned_sql.json, what production runs today), both run on Neon in the same
session so before and after are read on the same bank. Run from the repo root:

    python docs/receipts/cpc-api-flip-v2-d091693/make_explains.py
"""
import json, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
import cpc_outlooks as co  # noqa: E402

MAIN = json.loads((ROOT / "docs/receipts/cpc-api-method-pin-d091691/pinned_sql.json").read_text())
READS = [
    ("01", "CURVES_SQL", {"product": "610temp", "place_kind": "station", "place": "USW00023188", "weighting": "", "n": 1}),
    ("02", "CURVES_SQL", {"product": "814temp", "place_kind": "station", "place": "USW00023188", "weighting": "", "n": 1}),
    ("03", "CURVES_SQL", {"product": "814temp", "place_kind": "region", "place": "pnw", "weighting": "population", "n": 14}),
    ("04", "CURVES_SQL", {"product": "610temp", "place_kind": "station", "place": "USW00003017", "weighting": "", "n": 14}),
    ("05", "PLACES_SQL", {}),
    ("06", "PLACES_NEWEST_SQL", {"products": ["610temp", "814temp"]}),
]


def flat(s):
    return " ".join(s.split())


def lit(v):
    if isinstance(v, list):
        return "ARRAY[" + ",".join(f"'{x}'" for x in v) + "]"
    return str(v) if isinstance(v, int) else f"'{v}'"


def stmt(sql, p):
    return "EXPLAIN (ANALYZE, BUFFERS) " + re.sub(r"%\((\w+)\)s", lambda m: lit(p[m.group(1)]), flat(sql))


if __name__ == "__main__":
    out = ["-- d091693 plan receipts. Axx: the v2-pinned SQL (cpc_outlooks.py, CPC_METHOD_VERSION);",
           "-- Bxx: main a10b6f2's v1-pinned SQL (docs/receipts/cpc-api-method-pin-d091691/pinned_sql.json).",
           "-- Each with d091679's params for the same read (Cxx/Pxx there). Neon production, read-only.",
           ""]
    for n, name, p in READS:
        for side, sql in (("A", flat(getattr(co, name))), ("B", MAIN[name])):
            out.append(f"-- {side}{n} {name} {json.dumps(p)}")
            out.append(stmt(sql, p) + ";")
            out.append("")
    (HERE / "explains.sql").write_text("\n".join(out))
    print(len(READS) * 2, "statements")
