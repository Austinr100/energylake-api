"""The routes' own reads, run on Neon and on the local bank (`both`), each
wrapped so the database hashes its own answer:

    SELECT length(t) AS len, encode(sha256(convert_to(t, 'UTF8')), 'hex') AS sha
      FROM (SELECT coalesce(json_agg(row_to_json(q)), '[]')::text AS t
              FROM (<the read, literals substituted>) q) x

`python neon_reads.py sql` prints each wrapped statement (run on Neon through
the connector; the answers are in neon_reads.json). test_V1_the_local_bank_answers_as_neon
runs the same text on the local bank and holds the two equal: the bank the
bodies are served from is Neon's, read for read.
"""
import json, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
import cpc_outlooks as co  # noqa: E402

MAIN = json.loads((ROOT / "docs/receipts/cpc-api-method-pin-d091691/pinned_sql.json").read_text())
READS = [
    ("N1", "CURVES_SQL", "v2", {"product": "814temp", "place_kind": "station", "place": "USW00023188", "weighting": "", "n": 14}),
    ("N2", "CURVES_SQL", "v2", {"product": "610temp", "place_kind": "station", "place": "USW00003017", "weighting": "", "n": 14}),
    ("N3", "CURVES_SQL", "v2", {"product": "814temp", "place_kind": "region", "place": "caiso_sp15_proxy", "weighting": "load_share_365d", "n": 1}),
    ("N4", "PLACES_SQL", "v2", {}),
    ("N5", "PLACES_NEWEST_SQL", "v2", {"products": ["610temp", "814temp"]}),
    ("N6", "CURVES_SQL", "v1", {"product": "814temp", "place_kind": "region", "place": "pnw", "weighting": "population", "n": 14}),
]


def flat(s):
    return " ".join(s.split())


def lit(v):
    if isinstance(v, list):
        return "ARRAY[" + ",".join(f"'{x}'" for x in v) + "]"
    return str(v) if isinstance(v, int) else f"'{v}'"


def wrapped(name, side, p):
    sql = flat(getattr(co, name)) if side == "v2" else MAIN[name]
    inner = re.sub(r"%\((\w+)\)s", lambda m: lit(p[m.group(1)]), flat(sql))
    return ("SELECT length(t) AS len, encode(sha256(convert_to(t, 'UTF8')), 'hex') AS sha "
            "FROM (SELECT coalesce(json_agg(row_to_json(q)), '[]')::text AS t FROM ("
            + inner + ") q) x")


if __name__ == "__main__" and sys.argv[1:] == ["sql"]:
    for tag, name, side, p in READS:
        print(f"-- {tag}"); print(wrapped(name, side, p))
