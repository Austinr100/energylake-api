"""How bank/display.jsonl.gz and its four companions were read off the live ledger (d091746).

The display rows the Tropics index tests read are Neon's own rows, verbatim: no
display row is constructed any more (d091741 built them from the recon's parses
before d091740 published anything; the first backfill landed 2026-10-15 15:22Z).
The read is ONE read-only transaction through the Neon connector (project
Energylake, production branch): `SET TRANSACTION READ ONLY`, then STATEMENTS below,
each `row_to_json(t)::text` per row, joined by '\\n' in key order, with the
sha-256 Neon computed over that same text. No connection is held across any
other work; nothing is written.

    python3 -I bank_display_from_neon.py --sql          # the statements, as sent
    python3 -I bank_display_from_neon.py SAVED.json      # write bank/*.jsonl.gz and manifest.json's "d091746"

SAVED.json is the connector's saved result (a JSON list: [] for the SET, then one
[{"kind", "read_at", "ro", "n", "sha256", "body"}] per statement). Each body is
checked against Neon's sha and row count before anything is written.

WHAT IS READ (`IDENTITIES`): every display row, every revision, of
  * the identities the d091741 suite already reads (the ten advisory objects, the
    WSP bands and grids of 2026101112 and 2026101506);
  * the twelve real corrections' identities (`<ADV>` and `<ADV>r1`): al092026 016
    tcd/tcp; ep182026 052A, 053A, 054, 057 cone/ww and 056 cone; ep212026 001 cone;
  * Simon 033's advisory objects (T2) and WSP 2026101512 (I3), and WSP 2026101012
    (the cycle of Rachel 054, so the 054 body is the one Railway serves).
And, so Simon 033 is an advisory the routes can resolve (its synoptic time is
I3's cycle): the NHC rows of ep202026 033 the d091741 slice's product filter
admits, the two WSP zips of 2026101512, and Simon 033's official points. And the
two rows migration 316's pre- and post-checks need in the test cluster: the
`nhc_two_vintage` dataset (316's probe inserts under it) and schema_migrations
314 and 315 (316 refuses to run without both).

Not run by the suite; the suite re-checks every sha on load.
"""
import gzip
import hashlib
import json
import pathlib
import sys

FIX = pathlib.Path(__file__).resolve().parent

BASE = "'^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})'"      # migration 316's base pattern
ADVISORY = [("display:cone", s, a) for s, a in (
    ("ep202026", "032"), ("ep182026", "058"), ("ep182026", "052A"), ("ep182026", "053A"),
    ("ep182026", "054"), ("ep182026", "056"), ("ep182026", "057"), ("ep212026", "001"),
    ("ep202026", "033"))]
ADVISORY += [("display:ww", s, a) for s, a in (
    ("ep182026", "057A"), ("ep202026", "028A"), ("al092026", "014A"), ("ep182026", "052A"),
    ("ep182026", "053A"), ("ep182026", "054"), ("ep182026", "057"), ("ep202026", "033"))]
ADVISORY += [("display:surge_ww", "al092026", "014A"), ("display:surge_ww", "ep202026", "033")]
ADVISORY += [(f"display:{p}", s, a) for p in ("tcd", "tcp") for s, a in (
    ("ep182026", "058"), ("ep202026", "032"), ("al092026", "016"), ("ep202026", "033"))]
WSP = [(f"display:wsp{kt}_{k}", "", c) for c in ("2026101012", "2026101112", "2026101506", "2026101512")
       for kt in ("34", "50", "64") for k in ("bands", "grid")]
IDENTITIES = ADVISORY + WSP


def _values(ids) -> str:
    return ", ".join(f"('{p}', '{s}', '{a}')" for p, s, a in ids)


def _agg(kind: str, frm: str, order: str) -> str:
    return (f"SELECT '{kind}' AS kind, now() AS read_at, current_setting('transaction_read_only') AS ro, "
            f"count(*) AS n, encode(sha256(convert_to(string_agg(row_to_json(t)::text, E'\\n' "
            f"ORDER BY {order}), 'UTF8')), 'hex') AS sha256, string_agg(row_to_json(t)::text, E'\\n' "
            f"ORDER BY {order}) AS body FROM {frm}")


STATEMENTS = {
    "display": _agg("display", (
        f"tropical_file_vintage t JOIN (VALUES {_values(IDENTITIES)}) AS i(product, storm_id, base) "
        f"ON t.source = 'display' AND t.product = i.product AND t.storm_id = i.storm_id "
        f"AND substring(t.vintage_key FROM {BASE}) = i.base"), "t.vintage_id"),
    "ledger_033": _agg("ledger_033", (
        "tropical_file_vintage t WHERE t.source = 'nhc' AND ((t.storm_id = 'ep202026' AND t.product IN "
        "('cone_kmz', 'ww_kmz', 'surge_ww_kml', 'tcd', 'tcp', 'storm_entry') AND (t.vintage_key = '033' "
        "OR t.vintage_key LIKE '033:%')) OR (t.storm_id = '' AND t.product IN ('wsp_5km_zip', "
        "'wsp_halfdeg_zip') AND t.vintage_key = '2026101512'))"), "t.vintage_id"),
    "official_033": _agg("official_033", (
        "tropical_track_points t WHERE t.storm_id = 'ep202026' AND t.source = 'nhc_official' "
        "AND t.advisory = '033'"), "t.point_id"),
    "dataset_two": _agg("dataset_two", "datasets t WHERE t.dataset_code = 'nhc_two_vintage'",
                        "t.dataset_code"),
    "migrations": _agg("migrations", "schema_migrations t WHERE t.version IN (314, 315)", "t.version"),
    "count": ("SELECT 'count' AS kind, now() AS read_at, current_setting('transaction_read_only') AS ro, "
              "(SELECT count(*) FROM tropical_file_vintage) AS n_ledger, (SELECT count(*) FROM "
              "tropical_file_vintage WHERE source = 'display') AS n_display, (SELECT count(*) FROM "
              "tropical_file_vintage WHERE source = 'display' AND vintage_key ~ 'r[1-9][0-9]*$') AS "
              "n_display_revisions, md5(pg_get_viewdef('tropical_display_in_force'::regclass)) AS view_md5, "
              "(SELECT max(version) FROM schema_migrations) AS max_migration"),
}


def main(saved: str) -> None:
    res = json.load(open(saved))
    assert res[0] == [], "the first statement is SET TRANSACTION READ ONLY"
    got = {r[0]["kind"]: r[0] for r in res[1:]}
    assert sorted(got) == sorted(STATEMENTS), sorted(got)
    manifest = json.loads((FIX / "manifest.json").read_text())
    out = {"what": ("d091746: rows read verbatim off the live ledger (one read-only transaction, "
                    "bank_display_from_neon.py's STATEMENTS); every display row of the identities "
                    "listed, every revision"),
           "identities": [list(i) for i in IDENTITIES], "count": got["count"], "bank": {}}
    for kind, g in got.items():
        if kind == "count":
            assert g["ro"] == "on"
            continue
        assert g["ro"] == "on", kind
        text = g["body"]
        assert len(text.split("\n")) == int(g["n"]), kind
        sha = hashlib.sha256(text.encode()).hexdigest()
        if sha != g["sha256"]:
            raise SystemExit(f"{kind}: sha256 {sha} is not Neon's {g['sha256']}; nothing written")
        (FIX / "bank" / f"{kind}.jsonl.gz").write_bytes(gzip.compress(text.encode(), compresslevel=9, mtime=0))
        out["bank"][kind] = {"path": f"bank/{kind}.jsonl.gz", "rows": int(g["n"]), "sha256": sha,
                             "read_at": g["read_at"]}
        print(kind, g["n"], sha)
    manifest["d091746"] = out
    (FIX / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["--sql"]:
        print(json.dumps(["SET TRANSACTION READ ONLY"] + list(STATEMENTS.values()), indent=1))
    else:
        main(*sys.argv[1:])
