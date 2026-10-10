"""Write explains.sql: each CPC read that names cpc_outlook_curves,
cpc_curve_verdicts or v_cpc_curves_drawable, with d091679's params (its C01-C04,
P01, P02), twice: Axx is the pinned SQL in cpc_outlooks.py (this lane), Bxx is
main 891de2e's (d091679's pinned_sql.json), both run on Neon in the same
session so before and after are read on the same bank. Run from the repo root:

    python docs/receipts/cpc-api-method-pin-d091691/make_explains.py
"""
import json, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
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


out = ["-- d091691 plan receipts. Axx: the pinned SQL (cpc_outlooks.py, CPC_METHOD_VERSION);",
       "-- Bxx: main 891de2e's SQL (docs/receipts/cpc-outlooks-d091679/pinned_sql.json). Each",
       "-- with d091679's params for the same read (Cxx/Pxx there). Neon production, read-only.",
       ""]
for n, name, p in READS:
    for side, sql in (("A", flat(getattr(co, name))), ("B", MAIN[name])):
        out.append(f"-- {side}{n} {name} {json.dumps(p)}")
        out.append(stmt(sql, p) + ";")
        out.append("")
(HERE / "explains.sql").write_text("\n".join(out))
print(len(READS) * 2, "statements")
