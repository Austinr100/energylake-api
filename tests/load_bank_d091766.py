"""d091766: the AR bank the index routes read, for their tests.

tests/fixtures/ar_index_d091766/ holds, as Neon holds them (one read-only transaction
each, 2026-10-17 12:51-12:52Z; bank_from_neon.py, manifest.json "bank"):

  runs       every ar_runs row (16 cycles, 2026-10-09 12Z .. 2026-10-17 00Z)
  display    every ar_display_objects row (16 coast_ivt, 16 odds, 1 registry)
  landfall   every ar_landfall_latest row (6,603: 71 points x 93 steps, init 2026-10-17 00Z)

as Postgres's own row_to_json text, with the sha-256 Neon computed over it; the newest
coast_ivt object and the registry as display.energylake.io served them (objects/, sha = the
ledger row's); pantry migrations 145, 292 and 324 verbatim at af0aeb9 (ddl/, sha-pinned); and
Neon's own constraint definitions of the four tables (neon_constraints.json).

ar_scores held 0 rows at the read, so nothing of it is banked; SCORES_FIXTURE (A3) is the
one constructed row, labelled so, and is inserted through 292's CHECKs.

`cluster()` starts a throwaway Postgres (load_bank_d091673's), creates 145's ledger, a
datasets stand-in (324's post-check reads it; the pantry's rehearsal pattern,
scripts/rehearse_ar_publish_d091765.py), 292's sections 1-3 verbatim (its tables; sections
4-6 write datasets and views the cluster does not have), the ledger rows 289 and 292, then
324 VERBATIM, whole: its pre-check, its ten bites and its post-check run here. Then the banked
rows. Not a test module (no test_ prefix).
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import pathlib
import re
from datetime import datetime, timezone

import load_bank_d091673 as lb

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "ar_index_d091766"
MANIFEST = json.loads((FIX / "manifest.json").read_text())
NOW = datetime(2026, 10, 17, 13, 0, 0, tzinfo=timezone.utc)
LOAD_ORDER = ("runs", "display", "landfall")
TABLES = {"runs": "ar_runs", "display": "ar_display_objects", "landfall": "ar_landfall_latest"}

# A3's row: CONSTRUCTED (ar_scores is empty on Neon). One cell shown, shaped as 292's own
# post-check probe that lands (n 500, n_eff 120, bss_lo90 0.2, event_days_eff 20, no reasons).
SCORES_FIXTURE = {"scored_at": "2026-10-17T12:00:00+00:00", "scorer_hash": "fixture_d091766",
                  "show_rule": "show_v1", "level_set": "sub7_v1", "truth_level_set": "sub7_v1",
                  "region": "or_wa", "lead_bucket": "d1-2", "threshold": 250, "k_bin": "all",
                  "n": 500, "n_eff": 120, "bss_lo90": 0.2, "event_days_eff": 20, "show": True,
                  "show_reasons": []}


def text(kind: str) -> str:
    f = MANIFEST["bank"][kind]
    raw = gzip.decompress((FIX / f["path"]).read_bytes()).decode()
    got = hashlib.sha256(raw.encode()).hexdigest()
    if got != f["sha256"]:
        raise AssertionError(f"bank/{kind}: sha256 {got} is not the one Neon computed ({f['sha256']})")
    return raw


def rows(kind: str) -> list[dict]:
    return [json.loads(line) for line in text(kind).split("\n")]


def obj(name: str) -> bytes:
    """A display object as the bucket served it; its sha checked against the manifest."""
    f = MANIFEST["objects"][name]
    raw = gzip.decompress((FIX / f["path"]).read_bytes())
    got = hashlib.sha256(raw).hexdigest()
    if got != f["sha256"]:
        raise AssertionError(f"{f['path']}: sha256 {got} is not {f['sha256']}")
    return raw


def ddl(name: str) -> str:
    f = MANIFEST["ddl"][name]
    raw = (FIX / "ddl" / name).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != f["sha256"]:
        raise AssertionError(f"ddl/{name}: sha256 {got} is not pantry's ({f['sha256']})")
    return raw.decode()


def ddl_145_ledger() -> str:
    m = re.search(r"CREATE TABLE IF NOT EXISTS schema_migrations \(.*?\n\);",
                  ddl("145_schema_migrations_ledger.sql"), re.S)
    return m.group(0)


def ddl_292_tables() -> str:
    """292's sections 1-3 (ar_runs, ar_landfall_latest, ar_scores), verbatim."""
    t = ddl("292_ar_ivt_bank_shadow.sql")
    return t[t.index("-- 1. ar_runs"):t.index("-- 4. the datasets, born planned")]


def neon_constraints() -> list[dict]:
    return json.loads((FIX / "neon_constraints.json").read_text())


def insert(conn, table: str, objs) -> None:
    conn.execute(f"INSERT INTO {table} SELECT * FROM json_populate_recordset(NULL::{table}, %s::json)",
                 (json.dumps(objs) if not isinstance(objs, str) else objs,))


def load(conn) -> list[str]:
    """The cluster, as above; returns the notices 324 raised (its checks' own words)."""
    notices: list[str] = []
    conn.add_notice_handler(lambda d: notices.append(d.message_primary))
    conn.execute(ddl_145_ledger())
    conn.execute("CREATE TABLE datasets (dataset_code text PRIMARY KEY, lifecycle_status text, "
                 "freshness_mode text, config jsonb)")
    conn.execute(ddl_292_tables())
    for m in MANIFEST["neon"]["migrations"]:
        if m["version"] in (289, 292):
            conn.execute("INSERT INTO schema_migrations (version, filename, status, applied_at, attestation) "
                         "VALUES (%(version)s, %(filename)s, %(status)s, %(applied_at)s, %(attestation)s)", m)
    conn.execute(ddl("324_ar_display_objects_and_truth_witness.sql"))    # verbatim, whole
    for kind in LOAD_ORDER:
        insert(conn, TABLES[kind], "[" + ",".join(text(kind).split("\n")) + "]")
    conn.execute("ANALYZE")
    return notices


@contextlib.contextmanager
def cluster():
    with lb.cluster(load_bank=False) as conn:
        conn.notices_324 = load(conn)
        yield conn


PgPool = lb.PgPool
initdb_path = lb.initdb_path
