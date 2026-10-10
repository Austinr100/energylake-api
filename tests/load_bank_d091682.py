"""d091682: the tropical bank as the routes read it since pantry migrations 294
and 297 — the d091673 bank, with the revision column and the views of the rows
in force laid over it, and the ledger rows the routes read for `corrected`.

tests/fixtures/tropics_d091682/:
  bank/ledger.jsonl.gz   every tropical_file_vintage row at the d091673 cut but
                         the heartbeats (which that bank holds), as Neon's
                         row_to_json text (manifest.json): the routes read its
                         fcst_5day_zip, fcst_radii_zip and pws rows for
                         `corrected`; the rest makes the local table the size
                         Neon's was, so it is planned as Neon plans it
  bank/dataset.jsonl.gz  the ledger rows' datasets the d091673 bank lacks
  ddl_294.sql            pantry migration 294's schema change, verbatim
  ddl_297.sql            pantry migration 297's schema change, verbatim
  corrected.sql          the constructed corrections (R1-R5), applied only by
                         `construct`, never by the bank load

`in_force(conn)` is called by load_bank_d091673.load() after the bank's rows
are in, as on Neon: 294 then gives every row revision 0, then 297 restates the
views. `rows(kind)` checks the sha-256 Neon computed before it returns
anything, like d091673's.

Not a test module (no test_ prefix).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import pathlib

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "tropics_d091682"
MANIFEST = json.loads((FIX / "manifest.json").read_text())
TABLES = {"dataset": "datasets", "ledger": "tropical_file_vintage"}
LOAD_ORDER = ("dataset", "ledger")
OFFICIAL_FILES = ("fcst_5day_zip", "fcst_radii_zip")
ODDS_FILE = "pws"


def text(kind: str) -> str:
    f = MANIFEST["files"][kind]
    raw = gzip.decompress((FIX / f["path"]).read_bytes()).decode()
    got = hashlib.sha256(raw.encode()).hexdigest()
    if got != f["sha256"]:
        raise AssertionError(f"tropics_d091682 bank/{kind}: sha256 {got} is not the one Neon "
                             f"computed ({f['sha256']})")
    return raw


def lines(kind: str) -> list[str]:
    return text(kind).split("\n")


def rows(kind: str) -> list[dict]:
    return [json.loads(line) for line in lines(kind)]


def in_force(conn) -> None:
    """294 and 297 over the loaded bank, then the ledger slice."""
    conn.execute((FIX / "ddl_294.sql").read_text())
    conn.execute((FIX / "ddl_297.sql").read_text())
    for kind in LOAD_ORDER:
        conn.execute(f"INSERT INTO {TABLES[kind]} SELECT * FROM json_populate_recordset("
                     f"NULL::{TABLES[kind]}, %s::json)", ("[" + ",".join(lines(kind)) + "]",))


def construct(conn) -> None:
    """The constructed corrections (corrected.sql), then ANALYZE."""
    conn.execute((FIX / "corrected.sql").read_text())
    conn.execute("ANALYZE")


def ledger_corrected(ledger: list[dict], storm_id: str, advisory: str, products) -> bool:
    """The ledger's word, computed here without SQL: of each file identity, the
    copy in force is the newest banked one that is not an anomaly copy, and it is
    corrected when its meta carries `correction` (pantry 294's view, in Python)."""
    out = False
    for product in products:
        copies = [r for r in ledger if r["source"] == "nhc" and r["product"] == product
                  and r["storm_id"] == storm_id and r["vintage_key"] == advisory
                  and r["status"] == "banked" and "anomaly_prior_sha" not in (r["meta"] or {})]
        if copies:
            top = max(copies, key=lambda r: (r["fetch_ts"], r["vintage_id"]))
            out = out or "correction" in (top["meta"] or {})
    return out


# ── R6: the d091673 vectors, re-banked with the two fields and nothing else ──

def correction_objects(body: dict) -> list:
    """Every object of a Tropics body that carries `corrected` and `revision`:
    /storms' newest_advisory, /storm's official, /tracks' cycles[].official[],
    /odds' issuance."""
    out = [s["newest_advisory"] for s in body.get("storms", []) if s.get("newest_advisory")]
    if isinstance(body.get("official"), dict):
        out.append(body["official"])
    for c in body.get("cycles", []):
        out += c.get("official", []) if isinstance(c.get("official"), list) else []
    if isinstance(body.get("issuance"), dict):
        out.append(body["issuance"])
    return out


def strip_correction(body: dict) -> int:
    """Remove `corrected` and `revision` from every object that carries them, in
    place; return how many objects carried both."""
    n = 0
    for o in correction_objects(body):
        if "corrected" in o and "revision" in o:
            del o["corrected"], o["revision"]
            n += 1
    return n
