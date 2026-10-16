"""How bank/two.jsonl.gz, bank/toa.jsonl.gz and bank/nhc_toa.jsonl.gz were read off the live
ledger (d091757).

Rows read verbatim, as bank_display_from_neon.py (d091746) reads its own: ONE read-only
transaction through the Neon connector (project Energylake, production branch), `SET
TRANSACTION READ ONLY`, then STATEMENTS below, each `row_to_json(t)::text` per row joined by
'\\n' in vintage_id order, with the sha-256 Neon computed over that same text. Nothing is
constructed and nothing is written.

    python3 -I bank_outlook_toa_from_neon.py --sql          # the statements, as sent
    python3 -I bank_outlook_toa_from_neon.py SAVED.json      # write bank/*.jsonl.gz and manifest.json's "d091757"

SAVED.json is the connector's saved result (a JSON list: [] for the SET, then one
[{"kind", "read_at", "ro", "n", "sha256", "body"}] per statement). Each body is checked
against Neon's sha and row count before anything is written.

WHAT IS READ:
  two      every display:two_areas / two_points / two_text_at / two_text_ep row, every
           revision: the 7-day outlook as the publisher keyed it under migration 316 (the
           12-digit issuance stamp, D-09-25-234), storm_id ''.
  toa      every display:toa_earliest / toa_mostlikely row, every revision: the arrival-time
           objects as published, with whatever label table the row carries (the first
           publish's, or D-09-25-236's republish: `count` says which held at the read).
  nhc_toa  NHC's banked toa_earliest_kmz / toa_mostlikely_kmz rows of the four storms the
           suite reads (Isaias, Rachel, Simon, Trudy): the files the TOA objects name, so
           source_in_force and not_banked / not_published are read off real rows.
  count    the ledger at the read: rows, display rows, the TWO/TOA counts, how many TOA rows
           carry a label_rule (a key the publisher does not write: 0 expected), a withdrawal
           note, or a first parser's labels (no labels.by_source: the withdrawal's own
           selector, scripts/withdraw_toa_d091756.py), the view's md5, 316 applied.

Not run by the suite; the suite re-checks every sha on load (load_bank_d091741.rows).
"""
import gzip
import hashlib
import json
import pathlib
import sys

FIX = pathlib.Path(__file__).resolve().parent

TWO = ("display:two_areas", "display:two_points", "display:two_text_at", "display:two_text_ep")
TOA = ("display:toa_earliest", "display:toa_mostlikely")
NHC_TOA = ("toa_earliest_kmz", "toa_mostlikely_kmz")
STORMS = ("al092026", "ep182026", "ep202026", "ep212026")


def _in(xs) -> str:
    return "(" + ", ".join(f"'{x}'" for x in xs) + ")"


def _agg(kind: str, frm: str, order: str) -> str:
    return (f"SELECT '{kind}' AS kind, now() AS read_at, current_setting('transaction_read_only') AS ro, "
            f"count(*) AS n, encode(sha256(convert_to(string_agg(row_to_json(t)::text, E'\\n' "
            f"ORDER BY {order}), 'UTF8')), 'hex') AS sha256, string_agg(row_to_json(t)::text, E'\\n' "
            f"ORDER BY {order}) AS body FROM {frm}")


STATEMENTS = {
    "two": _agg("two", f"tropical_file_vintage t WHERE t.source = 'display' AND t.product IN {_in(TWO)}",
                "t.vintage_id"),
    "toa": _agg("toa", f"tropical_file_vintage t WHERE t.source = 'display' AND t.product IN {_in(TOA)}",
                "t.vintage_id"),
    "nhc_toa": _agg("nhc_toa", (f"tropical_file_vintage t WHERE t.source = 'nhc' AND t.product IN "
                                f"{_in(NHC_TOA)} AND t.storm_id IN {_in(STORMS)}"), "t.vintage_id"),
    "count": ("SELECT 'count' AS kind, now() AS read_at, current_setting('transaction_read_only') AS ro, "
              "(SELECT count(*) FROM tropical_file_vintage) AS n_ledger, "
              "(SELECT count(*) FROM tropical_file_vintage WHERE source = 'display') AS n_display, "
              f"(SELECT count(*) FROM tropical_file_vintage WHERE source = 'display' AND product IN {_in(TWO)}) "
              "AS n_two, "
              f"(SELECT count(*) FROM tropical_file_vintage WHERE source = 'display' AND product IN {_in(TOA)}) "
              "AS n_toa, "
              f"(SELECT count(*) FROM tropical_file_vintage WHERE source = 'display' AND product IN {_in(TOA)} "
              "AND meta ? 'label_rule') AS n_toa_label_rule, "
              f"(SELECT count(*) FROM tropical_file_vintage WHERE source = 'display' AND product IN {_in(TOA)} "
              "AND meta ? 'withdrawal') AS n_toa_withdrawn, "
              f"(SELECT count(*) FROM tropical_file_vintage WHERE source = 'display' AND product IN {_in(TOA)} "
              "AND NOT coalesce(meta -> 'labels' ? 'by_source', false)) AS n_toa_first_parser, "
              f"(SELECT count(*) FROM tropical_file_vintage WHERE source = 'display' AND product IN "
              f"{_in(TWO + TOA)} AND vintage_key ~ 'r[1-9][0-9]*$') AS n_two_toa_revisions, "
              "md5(pg_get_viewdef('tropical_display_in_force'::regclass)) AS view_md5, "
              "(SELECT max(version) FROM schema_migrations) AS max_migration"),
}


def main(saved: str) -> None:
    res = json.load(open(saved))
    assert res[0] == [], "the first statement is SET TRANSACTION READ ONLY"
    got = {r[0]["kind"]: r[0] for r in res[1:]}
    assert sorted(got) == sorted(STATEMENTS), sorted(got)
    manifest = json.loads((FIX / "manifest.json").read_text())
    out = {"what": ("d091757: rows read verbatim off the live ledger (one read-only transaction, "
                    "bank_outlook_toa_from_neon.py's STATEMENTS): every display:two_* and display:toa_* "
                    "row, every revision, and NHC's toa_*_kmz rows of the four storms"),
           "count": got["count"], "bank": {}}
    assert got["count"]["ro"] == "on"
    for kind, g in got.items():
        if kind == "count":
            continue
        assert g["ro"] == "on", kind
        text = g["body"]
        assert len(text.split("\n")) == int(g["n"]), kind
        sha = hashlib.sha256(text.encode()).hexdigest()
        if sha != g["sha256"]:
            raise SystemExit(f"{kind}: sha256 {sha} is not Neon's {g['sha256']}; nothing written")
        out["bank"][kind] = {"path": f"bank/{kind}.jsonl.gz", "rows": int(g["n"]), "sha256": sha,
                             "read_at": g["read_at"]}
    for kind, g in got.items():
        if kind != "count":
            (FIX / "bank" / f"{kind}.jsonl.gz").write_bytes(
                gzip.compress(g["body"].encode(), compresslevel=9, mtime=0))
            print(kind, g["n"], out["bank"][kind]["sha256"])
    manifest["d091757"] = out
    (FIX / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["--sql"]:
        print(json.dumps(["SET TRANSACTION READ ONLY"] + list(STATEMENTS.values()), indent=1))
    else:
        main(*sys.argv[1:])
