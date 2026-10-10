"""d091691: the CPC curve bank as the routes read it since pantry 298, with
cpc_curves_v2 rows constructed beside the banked v1 rows.

THE V1 BANK is d091679's, rebuilt as base rows. d091679's fixtures
(tests/fixtures/cpc_outlooks_d091679, each checked against the sha-256 Neon
computed) hold CURVES_SQL's answer for KSAN and pnw at both weightings over
each product's 14 newest issuances at the 2026-10-10 00:18Z cut. Every row of
those was drawable, so each base row is in it whole: the view's columns (`d.`)
give every value, and the window row's (`w_`) give reading, history, notes,
writer_version, source_r2_key, source_format_epoch and written_at, which are
constant across one (issuance, place). places_verdicts.json is the 720 cells
of the version in force, every column but verdict_id. places_newest.json is
the 34 window rows of each product's newest issuance; for the 14 stations
CURVES_SQL was not banked at, the identity and the cell are Neon's (that file)
and the value columns are KSAN's for the same issuance: no read here serves a
value of theirs (/places reads identity only), and their days are not loaded.
KDEN has no row, as on Neon (under 30 base years, v1 writes none).

THE V2 ROWS are constructed, as pantry's v2 writer (cpc_curves_writer_v2,
cpc_curve_verdicts_v2) would write them, each passing 298's CHECKs:
  * verdicts: every one of the 720 cells again as one v2 backtest_version,
    scored after v1's, method cpc_curves_v2 / b201e09ff97d, min_base_years 22,
    the count columns from the place's v1 history_years_in_base; a cell under
    30 says its count in band_sentence, and a pooled band under 30 becomes
    'none' (298: nothing borrowed from a 30-year neighbour);
  * curves BESIDE every banked issuance: a v2 window row and its days at KSAN,
    pnw x 2 and KDEN (v2 draws short places), the v1 values shifted (tavg
    +0.5, HDD/CDD +0.25) so a duplicate cannot pass for the original;
  * a V2-ONLY ISSUANCE newer than every v1 issuance of its product (610temp
    2026-10-10, 814temp 2026-10-09) at the same four places.

`build(conn, v2=..., newer=...)` creates 286 + 298 (ddl_286_298.sql, pantry's
text verbatim) and loads the rows with json_populate_recordset. The routes'
statements then run on it unchanged through load_bank_d091673.PgPool.

Not a test module (no test_ prefix).
"""

from __future__ import annotations

import contextlib
import copy
import gzip
import hashlib
import json
import pathlib
from datetime import date, timedelta

ROOT = pathlib.Path(__file__).resolve().parent
FIX = ROOT / "fixtures" / "cpc_method_pin_d091691"
BANK = ROOT / "fixtures" / "cpc_outlooks_d091679"

V1, V1_HASH = "cpc_curves_v1", "e99c71234013"
V2, V2_HASH = "cpc_curves_v2", "b201e09ff97d"     # pantry 298's header: the v2 method record
V2_BACKTEST = "cpcv_2026-10-03_d091691f1x7e"
V2_SCORED = "2026-10-10T15:00:00+00:00"
V2_WRITTEN = "2026-10-10T16:00:00+00:00"
NEWER = {"610temp": date(2026, 10, 10), "814temp": date(2026, 10, 9)}
LEAD = {"610temp": (6, 4), "814temp": (8, 6)}      # valid_start - issued, valid_end - valid_start

KSAN = ("station", "USW00023188", "")
KDEN = ("station", "USW00003017", "")
PNW_POP = ("region", "pnw", "population")
PNW_LOAD = ("region", "pnw", "load_share_365d")
FILES = {KSAN: "ksan", PNW_POP: "pnw_population", PNW_LOAD: "pnw_load_share_365d"}
PRODUCTS = ("610temp", "814temp")

VALUE_COLS = ("tavg_p05", "tavg_p25", "tavg_p50", "tavg_p75", "tavg_p95",
              "hdd_p05", "hdd_p25", "hdd_p50", "hdd_p75", "hdd_p95",
              "cdd_p05", "cdd_p25", "cdd_p50", "cdd_p75", "cdd_p95",
              "eq_tavg_p05", "eq_tavg_p50", "eq_tavg_p95",
              "eq_hdd_p05", "eq_hdd_p50", "eq_hdd_p95",
              "eq_cdd_p05", "eq_cdd_p50", "eq_cdd_p95")


def banked(name: str) -> list[dict]:
    """A d091679 fixture's rows as Neon's text gave them, sha-256 checked."""
    for line in (BANK / "manifest.psv").read_text().splitlines():
        f, n, sha, ln = line.split("|")
        if f == name:
            t = gzip.decompress((BANK / (f + ".gz")).read_bytes()).decode().rstrip("\n")
            if hashlib.sha256(t.encode()).hexdigest() != sha or len(t) != int(ln):
                raise AssertionError(f"{name}: not the text Neon computed its sha-256 over")
            rows = json.loads(t)
            assert len(rows) == int(n), name
            return rows
    raise KeyError(name)


# ── v1: d091679's bank as base rows ─────────────────────────────────────────

def _base_row(r: dict, place: tuple) -> dict:
    pk, pl, wt = place
    return {
        "product": None,                           # set by the caller
        "issued_date": r["issuance"], "valid_start": r["w_valid_start"],
        "valid_end": r["w_valid_end"], "place_kind": pk, "place": pl, "weighting": wt,
        "day_index": r["day_index"], "target_date": r["target_date"],
        **{k: r[k] for k in VALUE_COLS},
        "n_history_years": r["n_history_years"], "history_first": r["history_first"],
        "history_last": r["history_last"], "empty_class": r["empty_class"],
        "member_odds": r["member_odds"], "season": r["season"], "strength": r["strength"],
        "reading": r["w_reading"], "history": r["w_history"],
        "method_version": r["method_version"], "method_hash": r["method_hash"],
        "writer_version": r["w_writer_version"],
        "source_content_sha256": r["source_content_sha256"],
        "source_r2_key": r["w_source_r2_key"],
        "source_format_epoch": r["w_source_format_epoch"],
        "notes": r["w_notes"], "written_at": r["w_written_at"],
    }


def v1_curves() -> list[dict]:
    out = []
    for product in PRODUCTS:
        for place, f in FILES.items():
            for r in banked(f"curves_{product}_{f}.json"):
                # every banked row is written and drawable: the view's columns hold it whole
                assert r["w_season"] is not None and r["label"] is not None, (product, f)
                assert r["w_method_version"] == r["method_version"] == V1
                b = _base_row(r, place)
                b["product"] = product
                out.append(b)
    # the other written places' window rows at each product's newest issuance
    have = {(r["product"], r["issued_date"], r["place_kind"], r["place"], r["weighting"])
            for r in out}
    ksan_win = {(r["product"], r["issued_date"]): r for r in out
                if (r["place_kind"], r["place"], r["weighting"]) == KSAN
                and r["day_index"] is None}
    for n in banked("places_newest.json"):
        key = (n["product"], n["issued_date"], n["place_kind"], n["place"], n["weighting"])
        if key in have:
            continue
        w = dict(ksan_win[(n["product"], n["issued_date"])])
        w.update({k: n[k] for k in ("valid_start", "valid_end", "place_kind", "place",
                                     "weighting", "season", "strength", "method_version")})
        out.append(w)
    return out


def v1_verdicts() -> list[dict]:
    return banked("places_verdicts.json")


# ── v2: constructed beside it ───────────────────────────────────────────────

def _base_years(verdicts: list[dict]) -> dict:
    """Each place's base years: its v1 cells' history_years_in_base (one value
    per place)."""
    out = {}
    for v in verdicts:
        k = (v["place_kind"], v["place"], v["weighting"])
        out.setdefault(k, set()).add(v["history_years_in_base"])
    assert all(len(s) == 1 for s in out.values())
    return {k: s.pop() for k, s in out.items()}


def _members(place: tuple, curves: list[dict]) -> list[str]:
    pk, pl, wt = place
    if pk == "station":
        return [pl]
    odds = next((r["member_odds"] for r in curves
                 if (r["place_kind"], r["place"], r["weighting"]) == place), None)
    if odds:
        return sorted({m["station"] for m in odds})
    return [f"{pl}_member"]              # a region no banked curve names members of


def _count(place: tuple, years: int, curves: list[dict]) -> dict:
    by = [{"station": s, "base_years": years} for s in _members(place, curves)]
    return {"base_by_station": by, "base_fewest_stations": sorted(m["station"] for m in by),
            "threaded_base": []}


def v2_verdicts(v1: list[dict], curves: list[dict]) -> list[dict]:
    years = _base_years(v1)
    out = []
    for v in v1:
        place = (v["place_kind"], v["place"], v["weighting"])
        h = years[place]
        c = copy.deepcopy(v)
        c.update({"backtest_version": V2_BACKTEST, "scored_at": V2_SCORED,
                  "method_version": V2, "method_hash": V2_HASH,
                  "rules_version": "cpc_curve_verdicts_v2", "rules_hash": "d091691f1x7e",
                  "min_base_years": 22, "base_reason": None})
        cnt = _count(place, h, curves)
        c["base_by_station"], c["base_fewest_stations"] = cnt["base_by_station"], cnt["base_fewest_stations"]
        c["threaded_base"] = cnt["threaded_base"]
        if h < 30 and c["band_basis"] == "pooled_stations":
            c.update({"band_basis": "none", "band_basis_n": None, "band_basis_n_eff": None,
                      "band_share": None, "band_claim": None, "band_sentence": None})
        elif h < 30 and c["band_sentence"] is not None:
            c["band_sentence"] += f" Its normal rests on {h} base years, not CPC's 30."
        c["drawable"] = (c["verdict"] == "beats" and h >= c["min_base_years"]
                         and c["band_claim"] is not None)
        out.append(c)
    return out


def _shift(r: dict, days: int = 0) -> dict:
    c = copy.deepcopy(r)
    for k in VALUE_COLS:
        c[k] = c[k] + (0.5 if "tavg" in k else 0.25)
    if days:
        for k in ("issued_date", "valid_start", "valid_end", "target_date"):
            if c[k] is not None:
                c[k] = (date.fromisoformat(c[k]) + timedelta(days=days)).isoformat()
    return c


def v2_curves(v1c: list[dict], v1v: list[dict], *, newer: bool) -> list[dict]:
    years = _base_years(v1v)
    out = []
    banked_rows = [r for r in v1c if (r["place_kind"], r["place"], r["weighting"]) in FILES
                   ]
    for place in (KSAN, PNW_POP, PNW_LOAD, KDEN):
        src_place = KSAN if place == KDEN else place
        src = [r for r in banked_rows if (r["place_kind"], r["place"], r["weighting"]) == src_place]
        rows = []
        for r in src:
            c = _shift(r)
            c.update({"place_kind": place[0], "place": place[1], "weighting": place[2]})
            rows.append(c)
        if newer:
            for product in PRODUCTS:
                last = max(r["issued_date"] for r in rows if r["product"] == product)
                gap = (NEWER[product] - date.fromisoformat(last)).days
                rows += [_shift(r, gap) for r in rows
                         if r["product"] == product and r["issued_date"] == last]
        for c in rows:
            c.update({"method_version": V2, "method_hash": V2_HASH,
                      "writer_version": "cpc_curves_writer_v2", "written_at": V2_WRITTEN,
                      "base_years": years[place], **_count(place, years[place], v1c)})
        out += rows
    return out


# ── the cluster ─────────────────────────────────────────────────────────────

def _insert(conn, table: str, rows: list[dict]) -> None:
    cols = sorted({k for r in rows for k in r})
    conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) SELECT {', '.join(cols)} "
                 f"FROM json_populate_recordset(NULL::{table}, %s::json)",
                 (json.dumps(rows),))


def build(conn, *, v2: bool, newer: bool = True) -> dict:
    """286 + 298 on an empty database, the v1 bank, and (v2) the v2 rows.
    Returns the row counts loaded."""
    conn.execute((FIX / "ddl_286_298.sql").read_text())
    c1, vv1 = v1_curves(), v1_verdicts()
    _insert(conn, "cpc_curve_verdicts", vv1)
    _insert(conn, "cpc_outlook_curves", c1)
    n = {"v1_curves": len(c1), "v1_verdicts": len(vv1), "v2_curves": 0, "v2_verdicts": 0}
    if v2:
        vv2, c2 = v2_verdicts(vv1, c1), v2_curves(c1, vv1, newer=newer)
        _insert(conn, "cpc_curve_verdicts", vv2)
        _insert(conn, "cpc_outlook_curves", c2)
        n.update(v2_curves=len(c2), v2_verdicts=len(vv2))
    conn.execute("ANALYZE")
    return n


BANKS = {"v1": dict(v2=False), "v2": dict(v2=True, newer=True),
         "v2_beside_only": dict(v2=True, newer=False)}


@contextlib.contextmanager
def banks():
    """One throwaway cluster, one database per bank state; yields
    {name: connection}. Caller skips when load_bank_d091673.initdb_path() is None."""
    import psycopg
    from psycopg.rows import dict_row
    import load_bank_d091673 as lb
    with lb.cluster(load_bank=False) as admin:
        host = admin.info.host
        conns = {}
        try:
            for name, kw in BANKS.items():
                admin.execute(f"CREATE DATABASE bank_{name}")
                c = psycopg.connect(f"host={host} user=postgres dbname=bank_{name}",
                                    autocommit=True, row_factory=dict_row)
                conns[name] = c
                build(c, **kw)
            yield conns
        finally:
            for c in conns.values():
                c.close()
