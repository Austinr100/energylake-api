"""d091691: d091679's CPC bank as base rows in a throwaway Postgres, with
pantry 286 + 298 applied, and cpc_curves_v2 rows constructed beside it.

d091679 banked what its reads RETURNED, not the tables. Every column the
routes serve is in those rows, so the base rows are rebuilt from them:

  cpc_curve_verdicts   places_verdicts.json: all 720 cells, every column
                       PLACES_SQL selects (all of 286's but verdict_id).
  cpc_outlook_curves   curves_<product>_<place>.json for KSAN and pnw at both
                       weightings (14 issuances each): the window row's
                       identity and provenance from w_*, every value and the
                       label's inputs from the view's own row d.*. The other
                       13 written places (places_newest.json) get their newest
                       window row, values copied from KSAN's (never served).
                       KDEN is not written, as on Neon.

Every file is checked against the sha-256 Neon computed (d091679 manifest)
before a row is used. tests/test_cpc_method_pin_d091691.py first proves the
rebuild: main's statements over it serve d091679's vectors.

v2 is constructed in SQL (fixtures/cpc_method_pin_d091691/v2_beside.sql,
v2_newer.sql), as pantry's v2 writer would leave it, every row passing 298's
CHECKs. Not a test module (no test_ prefix).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import pathlib

import load_bank_d091673 as lb

ROOT = pathlib.Path(__file__).resolve().parent
BANK = ROOT / "fixtures" / "cpc_outlooks_d091679"
FIX = ROOT / "fixtures" / "cpc_method_pin_d091691"

PLACES = {"ksan": ("station", "USW00023188", ""),
          "pnw_population": ("region", "pnw", "population"),
          "pnw_load_share_365d": ("region", "pnw", "load_share_365d")}
PRODUCTS = ("610temp", "814temp")

initdb_path = lb.initdb_path
PgPool = lb.PgPool

VALUE_COLS = ("tavg_p05", "tavg_p25", "tavg_p50", "tavg_p75", "tavg_p95",
              "hdd_p05", "hdd_p25", "hdd_p50", "hdd_p75", "hdd_p95",
              "cdd_p05", "cdd_p25", "cdd_p50", "cdd_p75", "cdd_p95",
              "eq_tavg_p05", "eq_tavg_p50", "eq_tavg_p95",
              "eq_hdd_p05", "eq_hdd_p50", "eq_hdd_p95",
              "eq_cdd_p05", "eq_cdd_p50", "eq_cdd_p95")
CURVE_COLS = ("product", "issued_date", "valid_start", "valid_end", "place_kind", "place",
              "weighting", "day_index", "target_date", *VALUE_COLS, "n_history_years",
              "history_first", "history_last", "empty_class", "member_odds", "season",
              "strength", "reading", "history", "method_version", "method_hash",
              "writer_version", "source_content_sha256", "source_r2_key",
              "source_format_epoch", "notes", "written_at")
VERDICT_COLS = ("backtest_version", "scored_at", "truth_frontier", "method_version",
                "method_hash", "rules_version", "rules_hash", "product", "place_kind", "place",
                "weighting", "season", "strength", "min_n_eff", "n", "n_eff", "skill", "t",
                "strength_verdict", "share_p5_p95", "share_p25_p75", "season_n",
                "season_n_eff", "season_skill", "season_t", "verdict",
                "history_years_in_base", "band_basis", "band_basis_n", "band_basis_n_eff",
                "band_share", "band_claim", "band_sentence", "drawable")


def banked(name: str) -> list[dict]:
    """A d091679 bank file's rows as Neon returned them (JSON text), after the
    sha-256 and length check against its manifest."""
    for line in (BANK / "manifest.psv").read_text().splitlines():
        f, n, sha, ln = line.split("|")
        if f == name:
            t = gzip.decompress((BANK / (f + ".gz")).read_bytes()).decode().rstrip("\n")
            if hashlib.sha256(t.encode()).hexdigest() != sha or len(t) != int(ln):
                raise AssertionError(f"{f}: not the text Neon computed sha-256 {sha} over")
            rows = json.loads(t)
            assert len(rows) == int(n), f
            return rows
    raise KeyError(name)


def curve_rows() -> list[dict]:
    """cpc_outlook_curves rows, rebuilt from what CURVES_SQL returned."""
    out = []
    for product in PRODUCTS:
        ksan = None
        for f, (pk, pl, wt) in PLACES.items():
            rows = banked(f"curves_{product}_{f}.json")
            for r in rows:
                if r["label"] is None and r["day_index"] is None:
                    raise AssertionError(f"{f}: a banked frame with no view row")
                out.append({
                    "product": product, "issued_date": r["issuance"],
                    "valid_start": r["w_valid_start"], "valid_end": r["w_valid_end"],
                    "place_kind": pk, "place": pl, "weighting": wt,
                    "day_index": r["day_index"], "target_date": r["target_date"],
                    **{k: r[k] for k in VALUE_COLS},
                    **{k: r[k] for k in ("n_history_years", "history_first", "history_last",
                                         "empty_class", "member_odds", "season", "strength",
                                         "method_version", "method_hash",
                                         "source_content_sha256")},
                    "reading": r["w_reading"], "history": r["w_history"],
                    "writer_version": r["w_writer_version"],
                    "source_r2_key": r["w_source_r2_key"],
                    "source_format_epoch": r["w_source_format_epoch"],
                    "notes": r["w_notes"], "written_at": r["w_written_at"]})
            if f == "ksan":
                ksan = [o for o in out if o["product"] == product and o["place"] == pl]
        # the newest issuance's window row at every other written place
        newest = max(r["issued_date"] for r in ksan)
        win = next(r for r in ksan if r["issued_date"] == newest and r["day_index"] is None)
        have = {(pk, pl, wt) for pk, pl, wt in PLACES.values()}
        for n in banked("places_newest.json"):
            if n["product"] != product or (n["place_kind"], n["place"], n["weighting"]) in have:
                continue
            assert n["issued_date"] == newest, n
            out.append({**win, **{k: n[k] for k in ("valid_start", "valid_end", "place_kind",
                                                    "place", "weighting", "season", "strength",
                                                    "method_version")}})
    return out


def _insert(conn, table: str, cols, rows: list[dict]) -> None:
    c = ", ".join(cols)
    conn.execute(f"INSERT INTO {table} ({c}) SELECT {c} FROM json_populate_recordset("
                 f"NULL::{table}, %s::json)", (json.dumps([{k: r[k] for k in cols}
                                                          for r in rows]),))


def load(conn) -> None:
    """286 + 298 (verbatim), then the v1 bank."""
    conn.execute((FIX / "ddl_286.sql").read_text())
    conn.execute((FIX / "ddl_298.sql").read_text())
    _insert(conn, "cpc_curve_verdicts", VERDICT_COLS, banked("places_verdicts.json"))
    _insert(conn, "cpc_outlook_curves", CURVE_COLS, curve_rows())
    conn.execute("ANALYZE")


def construct(conn, *, newer: bool = True) -> None:
    """v2 beside v1 (v2_beside.sql); with `newer`, also a v2-only issuance
    newer than every v1 issuance (v2_newer.sql)."""
    conn.execute((FIX / "v2_beside.sql").read_text())
    if newer:
        conn.execute((FIX / "v2_newer.sql").read_text())
    conn.execute("ANALYZE")


cluster = lb.cluster   # cluster(load_bank=False), then load(conn)
