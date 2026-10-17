"""The river routes' reads and bodies: index, stations, flow, the NWM gate, reaches.

Lane d091770. Mirrors tropics_index.py: SQL constants here, pure `build_*` over the
rows, and main.py wires each route to one transaction under
`SET LOCAL statement_timeout` with a single-flight 300 s memo (D-09-25-75).

WHAT THIS MODULE MAY NOT READ (and what that costs)
───────────────────────────────────────────────────
The lane is read-only against Neon and READS NO R2. Neon's publish tables hold
only POINTERS: `geo_display_registry(registry_key, sha256, bytes, published_at,
schema)` and `geo_display_objects(object_id, display_key, sha256, bytes,
content_type, published_at)`. `bytes` is a bigint BYTE COUNT, not the body. The
bodies — the rivers registry and the NWM crosswalks — live on the display bucket.

Three things the route spec asks for are inside those bodies, so they are served
as stated absences, never derived (d091763 §5, measured 2026-10-17):

  * `length_km` — the crosswalk object's `reach.length_km` (the Columbia's 723.886
    km). `geo_layer_objects` HAS NO `length_km` COLUMN.
  * a station's `placed:{km, feature_id, dist_m}` — the crosswalk object's
    `places[<source_key>]`. Keyed by `record_sha256`, so the page joins it itself
    (D-09-25-237 §2); this module serves the pointer and the rule, not the join.
  * the river STRUCTURE — which walks make one river, its label and basin. That is
    the `geo.rivers.v1` registry's, and D-09-25-247 §3 forbids re-deriving it here.
    So the index is keyed on the WALK the ledger publishes, carries the river tag
    the records themselves spell (`attrs._bank.river`), and hands the page the
    registry pointer.

The anchor on a CWMS record is NOT a substitute. `attrs._bank.anchor
.km_from_reach_start` is an INTERVAL [lo, hi] on NHD's own `lengthkm` scale
(Bonneville [727.887, 728.371]); the walk's measure puts Bonneville at 723.746 km,
about 4.5 km away. A river without a crosswalk therefore serves the anchor with
its scale named and `placement: 'client_snaps_to_walk'` (D-09-25-237 §2).

PORTED FROM THE PANTRY, pinned to commit f2a75d16dcc0761f358b4ee8a33790e3d02256ac:
  * `geo/cwms.py` — SERIES, IRREGULAR, VERSIONS, the basins, `receipt_subject`,
    `scope_subject` (the settled-unit rule's scope: version, and relay basin).
  * `ingesters/usgs_iv.py` — `receipted_tags`.
  * `scripts/nwm_reach_gate.py` — `line_of`, `gate_line`, MIN_DAYS, TOL,
    LAYER_REACH (byte for byte; held against the dry-run stdout fixture).

EVERY timeseries_values READ names one `(dataset, series)` and takes it through
`LATERAL (… WHERE dataset = … AND series = … ORDER BY ts … LIMIT n)` — never a
cast in ORDER BY, never OR-ed series, never `DISTINCT ON` (D-08-02-L, d091551).
The same LATERAL shape takes the newest `geo_records` row per `source_key`.
"""

from __future__ import annotations

import statistics  # noqa: F401  (kept with the ported gate block's imports)
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# ── the published bucket (tropics_index.DISPLAY_BASE, the same host) ────────────
DISPLAY_BASE = "https://display.energylake.io/"

PANTRY_COMMIT = "f2a75d16dcc0761f358b4ee8a33790e3d02256ac"
RIVERS_REGISTRY_SCHEMA = "geo.rivers.v1"

# The window a flow or reaches read may ask for.
MAX_WINDOW_DAYS = 14

# ════════════════════════════════════ ported: geo/cwms.py @ PANTRY_COMMIT ══════

SERIES = {
    "cwms_forebay_hourly": "Elev-Forebay.Inst.1Hour.0.CBT-REV",
    "cwms_outflow_hourly": "Flow-Out.Ave.1Hour.1Hour.CBT-REV",
    "cwms_spill_hourly": "Flow-Spill.Ave.1Hour.1Hour.CBT-REV",
}
IRREGULAR = {
    "cwms_forebay_irregular": "Elev-Forebay.Inst.0.0.MIXED-REV",
    "cwms_outflow_irregular": "Flow-Out.Inst.0.0.MIXED-COMPUTED-REV",
}
ALL_SERIES = {**SERIES, **IRREGULAR}
DEFAULT_VERSION = "CBT-REV"

# THE VERSION IS PER PROJECT, NOT PER DATASET (d091718 on d091715 §9.2). A series read
# under a version named here is served only on a settled receipt of its own scope.
VERSIONS = {
    ("LGS", "cwms_outflow_hourly"): "Best",
    ("LMN", "cwms_outflow_hourly"): "Best",
    ("IHR", "cwms_outflow_hourly"): "Best",
}

WILLAMETTE = ("DET", "BCL", "LOP", "DEX", "HCR", "CGR", "BLU", "GPR", "FOS", "FAL",
              "DOR", "COT", "FRN")
MID_COLUMBIA = ("WEL", "RRH", "RIS", "WAN", "PRD")
BASINS = {"willamette": WILLAMETTE, "mid_columbia": MID_COLUMBIA}
# A RELAY IS ITS OWN SCOPE (D-09-25-212): the mid-Columbia numbers are the PUDs',
# relayed by USACE NWD, so they ride a subject of their own.
RELAY_BASINS = {"mid_columbia": "the Douglas, Chelan and Grant County PUDs' projects, "
                                "relayed by USACE NWD (CWMS NWDP)"}

# The USGS parameter each dataset serves (ingesters/usgs_iv.py).
USGS_DATASETS = {"usgs_iv_discharge": "00060", "usgs_iv_stage": "00065"}


def default_version(dataset: str) -> str:
    return ALL_SERIES[dataset].rsplit(".", 1)[1]


def version(loc: str, dataset: str) -> str:
    return VERSIONS.get((loc, dataset), default_version(dataset))


def ts_name(loc: str, dataset: str) -> str:
    return f"{loc}.{ALL_SERIES[dataset].rsplit('.', 1)[0]}.{version(loc, dataset)}"


def receipt_subject(dataset: str, ver: str, basin: Optional[str] = None) -> str:
    """`geo_unit_receipts.subject` for a dataset read under `ver`."""
    if basin in RELAY_BASINS:
        return f"{dataset}:{ALL_SERIES[dataset].rsplit('.', 1)[0]}.{ver}@{basin}"
    if ver == default_version(dataset):
        return dataset
    return f"{dataset}:{ALL_SERIES[dataset].rsplit('.', 1)[0]}.{ver}"


def relay_basin(loc: str) -> Optional[str]:
    return next((b for b in RELAY_BASINS if loc in BASINS[b]), None)


def scope_subject(loc: str, dataset: str) -> str:
    """The subject whose newest set must settle before `loc`'s series of `dataset`
    is served: its version's, and its relay basin's when it has one."""
    return receipt_subject(dataset, version(loc, dataset), relay_basin(loc))


# ═══════════════════════════ ported: ingesters/usgs_iv.py @ PANTRY_COMMIT ══════

def receipted_tags(rows: list) -> set:
    """The tags the subject's newest receipt set settles: a settled row on the tag and
    no contradicted row on it (a contradicted row naming no tag convicts every tag)."""
    contra = {t for t, v in rows if v == "contradicted"}
    if None in contra:
        return set()
    return {t for t, v in rows if v == "settled" and t is not None} - contra


# ═════════════════════ ported: scripts/nwm_reach_gate.py @ PANTRY_COMMIT ═══════

TOL = 0.20          # units.OUTFLOW_TOL
MIN_DAYS = 20       # units.MIN_DAYS
FT3 = 35.3147       # units.FT3_PER_M3
LAYER_REACH = {"nwm_crosswalk_columbia": "nhdplus_hr_columbia_gcl_bon",
               "nwm_crosswalk_snake": "nhdplus_hr_snake_lwg_columbia",
               "nwm_crosswalk_sacramento": "nhdplus_hr_sacramento_shasta_delta"}


def flow_verdict(stat, n: int) -> str:
    if stat is None or n < MIN_DAYS:
        return "inconclusive"
    return "settled" if abs(stat - 1.0) <= TOL else "contradicted"


def line_of(q: str, name: str, r: Optional[dict], why: Optional[str] = None) -> str:
    if r is None:
        return f"{q} {name}: {why}"
    hp = r["_detail"].get("hourly_pairs") or {}
    pairs = f", hour pairs {hp['range'][0]}–{hp['range'][1]}" if hp.get("range") else ""
    return f"{q} {name}: {r['verdict']} ({r['statistic']}, n {r['n_obs']}{pairs})"


def gate_line(reach_layer: str, items: list) -> tuple[str, bool]:
    """items: [(q, name, receipt | None, why)] in the reach's order. The recon's rule:
    drawable only when every item is settled; else the first that is not decides."""
    bad = [i for i in items if i[2] is None or i[2]["verdict"] != "settled"]
    if not items:
        return (f"{reach_layer}: not drawable — decided by Q2 crosswalk: no dam or gauge "
                f"placed on the reach"), False
    if bad:
        return f"{reach_layer}: not drawable — decided by {line_of(*bad[0])}", False
    lo = min(i[2]["statistic"] for i in items)
    hi = max(i[2]["statistic"] for i in items)
    n = min(i[2]["n_obs"] for i in items)
    return (f"{reach_layer}: NWM drawable — decided by {items[0][0]} every "
            f"{'dam' if items[0][0] == 'Q4' else 'gauge'}: "
            f"settled ({lo}–{hi}, n >= {n})"), True


# ════════════════════════════════════════════════════════════ the reads ════════
#
# Every read below is one statement inside the route's single transaction, after
# `SET LOCAL statement_timeout`. None reads R2. None uses `DISTINCT ON`: the newest
# row per key goes through `LATERAL … ORDER BY … LIMIT 1`, the form d091551 measured
# (`DISTINCT ON` reads every row of the group to keep one).

# THE NEWEST DERIVED OBJECT PER layer_code THAT IS PUBLISHED, and beside it the newest
# banked one of either kind. An UNPUBLISHED object is useless to the page (it fetches
# the bytes off the display bucket), so the served object is the newest PUBLISHED one;
# when a newer one is banked and not yet published, the body says so rather than
# silently serving the older sha or dropping the river. Measured 2026-10-17: the
# Columbia's nwm_crosswalk object 32 (fetched 11:27Z) is banked with no publish row,
# while object 9 is published — taking 'newest derived' alone would read no_crosswalk.
# NOTE there is no `length_km` column to select — see the module docstring.
WALKS_SQL = """
    WITH codes AS (
        SELECT DISTINCT layer_code FROM geo_layer_objects WHERE role = 'derived'
    )
    SELECT c.layer_code,
           p.object_id, p.r2_key, p.r2_sha256, p.feature_count, p.vertex_count,
           p.source_code, p.source_vintage, p.source_sha256, p.crs_as_published,
           p.simplification, p.tolerance_m, p.derived_from, p.derived_from_keys,
           p.terms_ref, p.fetched_at,
           p.display_key, p.published_sha256, p.published_bytes, p.content_type,
           p.published_at,
           n.object_id AS newest_object_id, n.r2_sha256 AS newest_r2_sha256,
           n.fetched_at AS newest_fetched_at
    FROM codes c
    CROSS JOIN LATERAL (
        SELECT g.object_id, g.r2_sha256, g.fetched_at
        FROM geo_layer_objects g
        WHERE g.layer_code = c.layer_code AND g.role = 'derived'
        ORDER BY g.fetched_at DESC, g.object_id DESC
        LIMIT 1
    ) AS n
    LEFT JOIN LATERAL (
        SELECT g.*, d.display_key, d.sha256 AS published_sha256,
               d.bytes AS published_bytes, d.content_type, d.published_at
        FROM geo_layer_objects g
        JOIN geo_display_objects d ON d.object_id = g.object_id
        WHERE g.layer_code = c.layer_code AND g.role = 'derived'
        ORDER BY g.fetched_at DESC, g.object_id DESC
        LIMIT 1
    ) AS p ON TRUE
"""

# The rivers registry in force: the newest published row of the rivers schema.
REGISTRY_SQL = """
    SELECT registry_key, sha256, bytes, published_at, schema
    FROM geo_display_registry
    WHERE schema = %(schema)s
    ORDER BY published_at DESC, registry_key DESC
    LIMIT 1
"""

# The newest record per (kind, source_code, source_key) — the ingesters' own rule
# (d091763 §2.5: geo_records holds two copies of every CWMS project, 57-66 superseded
# by 67-76). One LATERAL per key, never DISTINCT ON.
RECORDS_SQL = """
    WITH keys AS (
        SELECT DISTINCT kind, source_code, source_key FROM geo_records
    )
    SELECT r.record_id, r.kind, r.source_code, r.source_key, r.name, r.lat, r.lon,
           r.position_basis, r.attrs, r.record_sha256, r.source_sha256, r.fetched_at
    FROM keys k
    CROSS JOIN LATERAL (
        SELECT g.* FROM geo_records g
        WHERE g.kind = k.kind AND g.source_code = k.source_code
          AND g.source_key = k.source_key
        ORDER BY g.fetched_at DESC, g.record_id DESC
        LIMIT 1
    ) AS r
"""

# The NEWEST RECEIPT SET per subject: every row sharing the subject's newest
# measured_at. `receipted_tags` is then applied to that set alone (a receipt written
# under a subject replaces its newest set, so the set is the unit of decision).
RECEIPTS_SQL = """
    WITH subjects AS (
        SELECT DISTINCT subject FROM geo_unit_receipts
    )
    SELECT u.subject, u.receipt_id, u.unit_tag, u.unit_settled, u.verdict,
           u.n_obs, u.statistic, u.arbiter_kind, u.arbiter_ref,
           u.window_start, u.window_end, u.tolerance, u.expected, u.measured_at
    FROM subjects s
    CROSS JOIN LATERAL (
        SELECT max(g.measured_at) AS newest FROM geo_unit_receipts g
        WHERE g.subject = s.subject
    ) AS n
    JOIN geo_unit_receipts u
      ON u.subject = s.subject AND u.measured_at = n.newest
"""

# The forecast cycles a reach's product holds, for a drawable gate. One
# (dataset, series) per LATERAL, the series named in full.
FORECAST_CYCLE_SQL = """
    SELECT d.series,
           v.first_ts, v.last_ts, v.n
    FROM (VALUES %(values)s) AS d(series)
    CROSS JOIN LATERAL (
        SELECT min(t.ts) AS first_ts, max(t.ts) AS last_ts, count(*) AS n
        FROM timeseries_values t
        WHERE t.dataset = 'nwm_reach_forecast_hourly' AND t.series = d.series
    ) AS v
"""

# How many rows a window can hold, per dataset: the LIMIT each LATERAL carries.
CADENCE_S = {
    "cwms_forebay_hourly": 3600, "cwms_outflow_hourly": 3600, "cwms_spill_hourly": 3600,
    "cwms_forebay_irregular": 300, "cwms_outflow_irregular": 300,
    "usgs_iv_discharge": 900, "usgs_iv_stage": 900,
    "nwm_reach_analysis_hourly": 3600, "nwm_reach_forecast_hourly": 3600,
}
DEFAULT_CADENCE_S = 300


def window_limit(dataset: str, start: datetime, end: datetime) -> int:
    """The window's row count: its span over the dataset's own cadence, plus a row
    at each edge. Every LATERAL carries it, so no read can walk past the window."""
    span = max((end - start).total_seconds(), 0.0)
    return int(span // CADENCE_S.get(dataset, DEFAULT_CADENCE_S)) + 2


def values_sql(n: int, cols: int = 2) -> str:
    """`(%s, %s), (%s, %s), …` — the (dataset, series) pairs a read names."""
    one = "(" + ", ".join(["%s"] * cols) + ")"
    return ", ".join([one] * n)


def series_window_sql(n_pairs: int, *, order: str = "ASC") -> str:
    """One LATERAL per (dataset, series) over a ts window. NEVER a cast in ORDER BY,
    never OR-ed series, never DISTINCT ON (D-08-02-L).

    The banked unit tag rides along in the SELECT list (never in ORDER BY), so the
    body can state the tag the rows were served in and grey a series whose tag the
    receipts do not settle (D-08-02-R)."""
    if order not in ("ASC", "DESC"):
        raise ValueError(f"order must be ASC or DESC, got {order!r}")
    return f"""
    SELECT d.dataset, d.series, v.ts, v.value, v.unit_tag
    FROM (VALUES {values_sql(n_pairs)}) AS d(dataset, series)
    CROSS JOIN LATERAL (
        SELECT t.ts, t.value,
               COALESCE(t.meta->>'units', t.meta->>'unit_of_measure') AS unit_tag
        FROM timeseries_values t
        WHERE t.dataset = d.dataset AND t.series = d.series
          AND t.ts >= %s AND t.ts < %s
        ORDER BY t.ts {order}
        LIMIT %s
    ) AS v
"""


# ═══════════════════════════════════════════════ the ledger, shaped ════════════

NHD_PREFIX = "nhdplus_hr_"
XW_PREFIX = "nwm_crosswalk_"
# D-09-25-237 §2: the anchor's km is NHD's `lengthkm`, not the walk's own measure.
ANCHOR_SCALE = "nhd_lengthkm"
ANCHOR_SCALE_WHY = (
    "attrs._bank.anchor.km_from_reach_start is an interval [lo, hi] on NHD's lengthkm "
    "scale, not the walk geometry's measure (D-09-25-237 §2): on the Columbia the two "
    "differ by about 4.5 km (Bonneville [727.887, 728.371] against the walk's 723.746). "
    "So it is served as the anchor it is, and the page snaps by geometry."
)
PLACES_WHY = (
    "a station's km, feature_id and dist_m are in the crosswalk object's "
    "places[<source_key>], which is on the display bucket; this route reads no R2 "
    "(d091763 §5). The object is keyed by record_sha256, so the page joins it against "
    "each record's own sha and applies: placed iff the place exists, its "
    "record_sha256 equals the record's, and dist_m <= 1000."
)
LENGTH_KM_WHY = (
    "length_km is the crosswalk object's reach.length_km (the Columbia's 723.886 km); "
    "geo_layer_objects has no length_km column and this route reads no R2."
)
STRUCTURE_WHY = (
    "a river's label, basin and which walks compose it are the geo.rivers.v1 registry's, "
    "and D-09-25-247 §3 forbids re-deriving the river structure here; the registry "
    "pointer below is served so the page reads it. `river` is the tag the records "
    "themselves carry (attrs._bank.river), or the walk's own key when none does."
)
HOURLY_PAIRS_WHY = (
    "geo_unit_receipts banks no _detail column, so a gate line recomputed from banked "
    "receipts carries no 'hour pairs' clause; the pantry's stdout has it because it is "
    "computed in-process. Its own `receipt ...` lines are this same shape."
)


def url_for(key: Optional[str]) -> Optional[str]:
    return None if not key else DISPLAY_BASE + key


def bank_of(rec: dict) -> dict:
    return ((rec.get("attrs") or {}).get("_bank") or {})


def walk_key(layer_code: str) -> str:
    """`nhdplus_hr_columbia_gcl_bon` -> `columbia_gcl_bon` (the crosswalk's reach.key)."""
    return layer_code[len(NHD_PREFIX):] if layer_code.startswith(NHD_PREFIX) else layer_code


def record_walk(rec: dict) -> Optional[str]:
    """The walk a record sits on: a CWMS project's anchor reach, a gauge's banked reach."""
    b = bank_of(rec)
    anchor = b.get("anchor") or {}
    return anchor.get("reach") or b.get("reach")


def published_of(row: dict) -> Optional[dict]:
    """The publish row as served, or None when the object is banked but not published."""
    if not row.get("display_key"):
        return None
    return {"display_key": row["display_key"], "sha256": row.get("published_sha256"),
            "bytes": row.get("published_bytes"), "content_type": row.get("content_type"),
            "published_at": row.get("published_at"),
            "url": url_for(row["display_key"])}


def object_of(row: dict) -> dict:
    """A derived ledger object as served: the newest PUBLISHED one, with its pointer, and
    the newest banked object named when it is not the same (a publish is pending). No
    bytes: this reads no R2. A layer with nothing published is an absence, not a row."""
    newest = {"object_id": row.get("newest_object_id"),
              "r2_sha256": row.get("newest_r2_sha256"),
              "fetched_at": row.get("newest_fetched_at")}
    if row.get("object_id") is None:
        return {"absence": "not_published", "layer_code": row["layer_code"],
                "newest_banked": newest,
                "why": (f"{row['layer_code']} is banked (object_id "
                        f"{newest['object_id']}, r2_sha256 {newest['r2_sha256']}) but no "
                        f"derived object of it has a geo_display_objects row: nothing is "
                        f"published, so the page has no bytes to fetch")}
    pub = published_of(row)
    pending = (newest["object_id"] != row["object_id"])
    return {
        "layer_code": row["layer_code"], "object_id": row["object_id"],
        "role": "derived", "r2_key": row["r2_key"], "r2_sha256": row["r2_sha256"],
        "feature_count": row.get("feature_count"), "vertex_count": row.get("vertex_count"),
        "crs_as_published": row.get("crs_as_published"),
        "simplification": row.get("simplification"), "tolerance_m": row.get("tolerance_m"),
        "derived_from": row.get("derived_from"),
        "derived_from_keys": row.get("derived_from_keys"),
        "source": {"code": row.get("source_code"), "vintage": row.get("source_vintage"),
                   "sha256": row.get("source_sha256"), "terms_ref": row.get("terms_ref")},
        "fetched_at": row.get("fetched_at"),
        "published": pub,
        "url": (pub or {}).get("url"),
        "sha256": row["r2_sha256"],
        "sha_equal": None if pub is None else (pub.get("sha256") == row["r2_sha256"]),
        "served": "the newest derived object with a publish row",
        "newer_banked_not_published": (None if not pending else {
            **newest,
            "why": ("a newer derived object of this layer is banked but has no "
                    "geo_display_objects row; the published one above is served until it "
                    "is published")}),
    }


def shape_ledger(walk_rows: list, record_rows: list) -> dict:
    """The walks, their crosswalks and their records, keyed by the reach's layer_code.

    A crosswalk is tied to its walk by the ledger's OWN link, `derived_from` (the
    crosswalk object is derived from the reach object), not by a name table."""
    walks, xws = {}, {}
    for row in walk_rows or []:
        code = row["layer_code"]
        if code.startswith(XW_PREFIX):
            xws[row["object_id"]] = row
        elif code.startswith(NHD_PREFIX):
            walks[code] = row
    by_object = {w["object_id"]: code for code, w in walks.items()
                 if w.get("object_id") is not None}
    xw_for = {}
    for row in xws.values():
        reach_code = by_object.get(row.get("derived_from"))
        if reach_code is None:
            # The crosswalk's own name table says which reach it belongs to (the
            # pantry's LAYER_REACH); used only when derived_from names no banked walk.
            reach_code = LAYER_REACH.get(row["layer_code"])
        if reach_code is not None:
            xw_for[reach_code] = row
    records = {}
    for rec in record_rows or []:
        code = record_walk(rec)
        if code is not None:
            records.setdefault(code, []).append(rec)
    return {"walks": walks, "crosswalks": xw_for, "records": records}


def river_tag(records: list, layer_code: str) -> tuple[str, str]:
    """The river a walk's records name, and how it was named. `attrs._bank.river` when a
    record carries one (columbia, snake, or a Willamette walk name); else the walk's own
    key, with the reason. NEVER an invented grouping — that is the registry's (D-09-25-247 §3)."""
    tags = sorted({b for b in (bank_of(r).get("river") for r in records or []) if b})
    if len(tags) == 1:
        return tags[0], "attrs._bank.river"
    if len(tags) > 1:
        return tags[0], f"attrs._bank.river (the walk's records name {len(tags)}: {tags})"
    return walk_key(layer_code), ("the walk's own key: no record on it carries "
                                  "attrs._bank.river (a gauge banks attrs._bank.reach only)")


def resolve_river(requested: str, ledger: dict) -> tuple[Optional[str], list, Optional[str]]:
    """`?river=` -> the reach layer_code it names. Accepts the river tag (columbia), the
    walk's layer_code, the walk's key (columbia_gcl_bon), or an unambiguous first segment
    of the key (sacramento, willamette). Returns (layer_code, candidates, why)."""
    want = (requested or "").strip().lower()
    if not want:
        return None, [], "river is required"
    exact, prefix = [], []
    for code, row in sorted(ledger["walks"].items()):
        key = walk_key(code)
        tag, _ = river_tag(ledger["records"].get(code, []), code)
        if want in (code.lower(), key.lower(), tag.lower()):
            exact.append(code)
        elif key.lower().split("_")[0] == want:
            prefix.append(code)
    if len(exact) == 1:
        return exact[0], exact, None
    hit = exact or prefix
    if len(hit) == 1:
        return hit[0], hit, None
    if not hit:
        return None, [], (f"no banked walk is named {requested!r}; "
                          f"GET /api/hydro/rivers/index lists what is served")
    return None, hit, (f"{requested!r} names {len(hit)} banked walks ({', '.join(hit)}); "
                       f"name one of them. Which walks compose one river is the "
                       f"geo.rivers.v1 registry's, not this route's (D-09-25-247 §3)")


def crosswalk_served(xw_row: Optional[dict]) -> dict:
    """The crosswalk term: the pointer when one is banked AND published, else the absence
    D-09-25-247 §3 names (a crosswalk is optional; without one the NWM terms read
    no_crosswalk with the reason)."""
    if xw_row is None:
        return {"absence": "no_crosswalk",
                "why": ("no nwm_crosswalk object in geo_layer_objects is derived from "
                        "this walk")}
    obj = object_of(xw_row)
    if obj.get("absence"):
        return {"absence": "no_crosswalk", "why": obj["why"],
                "newest_banked": obj.get("newest_banked")}
    return obj


# ═══════════════════════════════════════════════════ /index ════════════════════

def build_index(walk_rows: list, registry_row: Optional[dict], record_rows: list) -> dict:
    """The rivers the bank serves, one entry per banked AND published walk.

    D-09-25-247 §3: a river is served when its reach object is banked and published; a
    crosswalk is optional, and without one the NWM terms read `no_crosswalk` with the
    reason. A walk that is banked but NOT published is omitted, with its reason.

    `length_km` and the river STRUCTURE (label, basin, which walks make one river) are in
    the published bodies, so they are served as absences with the pointer beside them.
    """
    ledger = shape_ledger(walk_rows, record_rows)
    registry = None
    if registry_row:
        registry = {"registry_key": registry_row["registry_key"],
                    "sha256": registry_row["sha256"], "bytes": registry_row["bytes"],
                    "schema": registry_row["schema"],
                    "published_at": registry_row["published_at"],
                    "url": url_for(registry_row["registry_key"])}

    rivers, omitted = [], []
    for code, row in sorted(ledger["walks"].items()):
        recs = ledger["records"].get(code, [])
        tag, how = river_tag(recs, code)
        reach = object_of(row)
        if reach.get("absence"):
            omitted.append({"river": tag, "walk": code,
                            "absence": "walk_not_published", "why": reach["why"],
                            "newest_banked": reach.get("newest_banked")})
            continue
        xw = crosswalk_served(ledger["crosswalks"].get(code))
        rivers.append({
            "river": tag, "river_named_by": how,
            "label": None, "basin": None,
            "structure": {"absence": "registry_not_read", "why": STRUCTURE_WHY},
            "walks": [reach],
            "length_km": None,
            "length_km_absence": {"absence": "in_crosswalk_object", "why": LENGTH_KM_WHY},
            "crosswalk": xw,
            "nwm": ({"absence": "no_crosswalk", "why": xw["why"]}
                    if xw.get("absence") else {"crosswalk": xw["layer_code"],
                                               "gate": "/api/hydro/rivers/gate"}),
            "registry": registry,
            "stations_in_ledger": len(recs),
        })
    return {
        "count": len(rivers), "rivers": rivers, "omitted": omitted,
        "registry": registry,
        "registry_absence": (None if registry else
                             {"absence": "no_rivers_registry",
                              "why": (f"geo_display_registry holds no "
                                      f"{RIVERS_REGISTRY_SCHEMA} row")}),
        "rule": ("D-09-25-247 §3: a river is served when its reach object is banked and "
                 "published; a crosswalk is optional, and without one the NWM terms read "
                 "no_crosswalk with the reason."),
        "display_base": DISPLAY_BASE,
        "reads_no_r2": True,
        "not_served": {
            "length_km": LENGTH_KM_WHY,
            "label/basin/river composition": STRUCTURE_WHY,
        },
        "ported_from": {"commit": PANTRY_COMMIT,
                        "nwm_reach_gate.LAYER_REACH": sorted(LAYER_REACH)},
    }


# ═══════════════════════════════════════════════════ /stations ═════════════════

def station_series(rec: dict) -> tuple[dict, dict]:
    """The (dataset -> served tag) a record's own bank names, and what it says is not
    served. A CWMS project banks both tables; a gauge's datasets come from the parameter
    codes its catalog holds."""
    b = bank_of(rec)
    if rec["kind"] == "cwms_project":
        return dict(b.get("series") or {}), dict(b.get("not_served") or {})
    if rec["kind"] == "gauge":
        codes = set((rec.get("attrs") or {}).get("time_series") or {})
        served = {ds: f"{rec['source_key']} {pc}"
                  for ds, pc in sorted(USGS_DATASETS.items()) if pc in codes}
        missing = {ds: f"{rec['source_key']} {pc} is not in the gauge's banked catalog"
                   for ds, pc in sorted(USGS_DATASETS.items()) if pc not in codes}
        return served, missing
    return {}, {}


def build_stations(river: str, layer_code: str, walk_rows: list, record_rows: list) -> dict:
    """The newest record per source_key on the river's walk, and how each is placed.

    With a crosswalk: the place is the crosswalk object's `places[<source_key>]`, keyed by
    `record_sha256` (D-09-25-237). This route reads no R2, so it serves the pointer, the
    record's sha and the rule, and the page — which fetches and sha-checks that object
    anyway — does the join. Without a crosswalk: the record's own lat/lon and anchor, with
    the anchor's scale named and `placement: 'client_snaps_to_walk'` (D-09-25-237 §2).
    """
    ledger = shape_ledger(walk_rows, record_rows)
    walk_row = ledger["walks"].get(layer_code)
    xw = crosswalk_served(ledger["crosswalks"].get(layer_code))
    has_xw = not xw.get("absence")
    recs = ledger["records"].get(layer_code, [])

    stations = []
    for rec in sorted(recs, key=lambda r: (r["kind"], r["source_key"])):
        b = bank_of(rec)
        anchor_raw = b.get("anchor") or {}
        anchor = None
        if anchor_raw:
            anchor = {
                "reach": anchor_raw.get("reach"),
                "reach_object_sha256": anchor_raw.get("reach_object_sha256"),
                "km_from_reach_start": anchor_raw.get("km_from_reach_start"),
                "km_is_an_interval": isinstance(anchor_raw.get("km_from_reach_start"), list),
                "distance_m": anchor_raw.get("distance_m"),
                "walk_index": anchor_raw.get("walk_index"),
                "hydroseq": anchor_raw.get("hydroseq"),
                "nhdplusid": anchor_raw.get("nhdplusid"),
                "scale": ANCHOR_SCALE, "scale_why": ANCHOR_SCALE_WHY,
            }
        served, not_served = station_series(rec)
        st = {
            "source_key": rec["source_key"], "kind": rec["kind"],
            "source_code": rec["source_code"], "name": rec["name"],
            "lat": rec["lat"], "lon": rec["lon"],
            "position_basis": rec["position_basis"],
            "record_sha256": rec["record_sha256"], "record_id": rec["record_id"],
            "fetched_at": rec["fetched_at"],
            "rule": b.get("rule"),
            "anchor": anchor,
            "anchor_absence": (None if anchor else
                               {"absence": "no_anchor",
                                "why": (f"a {rec['kind']} record banks no "
                                        f"attrs._bank.anchor; it banks its reach and its "
                                        f"distance to the centreline")}),
            "distance_to_centreline_m": b.get("distance_to_centreline_m"),
            "serves": served, "not_served": not_served,
        }
        if has_xw:
            st["placement"] = "join_crosswalk_places"
            st["placed"] = None
            st["placed_absence"] = {
                "absence": "in_crosswalk_object", "why": PLACES_WHY,
                "join": {"object": xw["layer_code"], "url": xw["url"],
                         "sha256": xw["sha256"],
                         "path": f"places[{rec['source_key']!r}]",
                         "key": "record_sha256", "record_sha256": rec["record_sha256"],
                         "placed_iff": ("the place exists AND its record_sha256 equals "
                                        "this record's AND dist_m <= 1000")},
            }
        else:
            st["placement"] = "client_snaps_to_walk"
            st["placed"] = None
            st["unplaced"] = {"why": xw["why"], "absence": "no_crosswalk",
                              "snap": ("the page snaps by geometry against the banked walk; "
                                       "the anchor's km is NHD's scale, not the walk's "
                                       "(D-09-25-237 §2)"),
                              "scale": ANCHOR_SCALE}
        stations.append(st)

    return {
        "river": river, "walk": layer_code,
        "walk_object": (object_of(walk_row) if walk_row else None),
        "walk_absence": (None if walk_row else
                         {"absence": "no_banked_walk",
                          "why": f"geo_layer_objects has no derived {layer_code} object"}),
        "crosswalk": xw,
        "count": len(stations), "stations": stations,
        "placement": ("join_crosswalk_places" if has_xw else "client_snaps_to_walk"),
        "newest_record_rule": ("the ingesters' own: the newest row per (kind, source_code, "
                              "source_key) by fetched_at, then record_id, read through one "
                              "LATERAL per key (geo_records holds two copies of every CWMS "
                              "project; 57-66 are superseded by 67-76 and never served)"),
        "reads_no_r2": True,
        "not_served": {"placed": PLACES_WHY} if has_xw else {},
    }


# ═══════════════════════════════════════════════════ the window ═══════════════

DEFAULT_WINDOW = timedelta(days=2)


def parse_window(raw_from: Optional[str], raw_to: Optional[str], *,
                 now: datetime) -> tuple[datetime, datetime]:
    """`from`/`to` as ISO instants or dates, UTC. The window is at most
    MAX_WINDOW_DAYS; beyond it the read is REFUSED, never silently clipped."""
    def one(raw: str, what: str) -> datetime:
        try:
            t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"{what}={raw!r} is not an ISO instant or date")
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

    end = one(raw_to, "to") if raw_to else now
    start = one(raw_from, "from") if raw_from else end - DEFAULT_WINDOW
    if start >= end:
        raise ValueError(f"from={start.isoformat()} is not before to={end.isoformat()}")
    if end - start > timedelta(days=MAX_WINDOW_DAYS):
        raise ValueError(
            f"the window {start.isoformat()} -> {end.isoformat()} is "
            f"{(end - start).days} days; at most {MAX_WINDOW_DAYS} are served "
            f"(each read's LATERAL carries the window's row count as its LIMIT)")
    return start, end


# ═══════════════════════════════════════════════════ the receipts ════════════

def receipt_sets(receipt_rows: list) -> dict:
    """subject -> {tags, settled, rows, measured_at, receipt_ids}: the NEWEST SET only.
    `tags` is `receipted_tags` over that set (ingesters/usgs_iv.py)."""
    by: dict = {}
    for r in receipt_rows or []:
        by.setdefault(r["subject"], []).append(r)
    out = {}
    for subject, rows in by.items():
        pairs = [(r["unit_tag"], r["verdict"]) for r in rows]
        out[subject] = {
            "subject": subject,
            "tags": receipted_tags(pairs),
            "receipt_ids": sorted(r["receipt_id"] for r in rows),
            "measured_at": max(r["measured_at"] for r in rows),
            "unit_settled": sorted({r["unit_settled"] for r in rows if r["unit_settled"]}),
            "verdicts": sorted({r["verdict"] for r in rows}),
            "rows": rows,
        }
    return out


def subject_of(rec: dict, dataset: str) -> tuple[str, str, Optional[dict]]:
    """The receipt subject a record's series is held to, and where it came from.

    THE BANK'S OWN WORD WINS. `attrs._bank.receipt_scope[<dataset>]` is the subject the
    ingester itself banked the series under (measured 2026-10-17 on the mid-Columbia
    relay, d091775), so it is used as stated. The ported `geo/cwms.scope_subject` is the
    fallback for a record that states none, and ALSO the cross-check: when both exist and
    differ, the disagreement is served rather than silently resolved."""
    if rec["kind"] != "cwms_project":
        return dataset, "the dataset code (ingesters/usgs_iv receipts the dataset)", None
    loc = rec["source_key"]
    ported = scope_subject(loc, dataset)
    stated = (bank_of(rec).get("receipt_scope") or {}).get(dataset)
    if stated is None:
        return ported, "geo/cwms.scope_subject (ported); the record states no receipt_scope", None
    if stated == ported:
        return stated, "attrs._bank.receipt_scope (equals the ported geo/cwms.scope_subject)", None
    return stated, "attrs._bank.receipt_scope", {
        "stated": stated, "ported": ported,
        "why": ("the record's banked receipt_scope and the ported "
                "geo/cwms.scope_subject disagree; the bank's own word is served and the "
                "disagreement is stated, never resolved here")}


def flow_pairs(stations: list) -> list:
    """The `(dataset, series)` each station serves — one pair per read, never OR-ed.
    The series is the bank's own: the bare CWMS location, or the USGS site key."""
    pairs = []
    for st in stations:
        for dataset in sorted(st["serves"]):
            pairs.append((dataset, st["source_key"], st))
    return pairs


def build_flow(river: str, layer_code: str, walk_rows: list, record_rows: list,
               receipt_rows: list, value_rows: list, gate: dict, *,
               start: datetime, end: datetime) -> dict:
    """Per station, the series it serves — ONLY when the subject's newest receipt set
    settles the tag the rows were served in (D-09-25-232 §2, D-08-02-R). Otherwise
    `{state: 'grey', why}`. A series is never served on another scope's receipt: the
    subject carries the version and the relay basin (geo/cwms.scope_subject).

    The NWM term needs each placed reach's `feature_id`, which is in the crosswalk
    object; this route reads no R2, so it is an absence beside the gate's own line.
    NWM VELOCITY IS NEVER READ (D-09-25-237).
    """
    ledger = shape_ledger(walk_rows, record_rows)
    recs = ledger["records"].get(layer_code, [])
    xw = crosswalk_served(ledger["crosswalks"].get(layer_code))
    sets = receipt_sets(receipt_rows)

    banked: dict = {}
    for row in value_rows or []:
        banked.setdefault((row["dataset"], row["series"]), []).append(row)

    stations = []
    for rec in sorted(recs, key=lambda r: (r["kind"], r["source_key"])):
        loc = rec["source_key"]
        b = bank_of(rec)
        served_map, not_served_map = station_series(rec)
        series_out = []
        for dataset in sorted(set(served_map) | set(not_served_map)):
            is_cwms = rec["kind"] == "cwms_project"
            subject, subject_from, disagreement = subject_of(rec, dataset)
            rset = sets.get(subject)
            tag_of = served_map.get(dataset) or not_served_map.get(dataset)
            common = {"dataset": dataset, "series": loc, "subject": subject,
                      "subject_from": subject_from,
                      "subject_disagreement": disagreement,
                      "served_tag": tag_of,
                      "scope": ({"version": (b.get("versions") or {}).get(dataset)
                                            or version(loc, dataset),
                                 "version_ported": version(loc, dataset),
                                 "relay_basin": relay_basin(loc),
                                 "basin": b.get("basin")} if is_cwms else
                                {"parameter_code": USGS_DATASETS.get(dataset)})}
            if dataset in not_served_map:
                series_out.append({**common, "state": "grey",
                                   "why": f"not served: {not_served_map[dataset]}"})
                continue
            rows = banked.get((dataset, loc)) or []
            if rset is None or not rset["tags"]:
                series_out.append({**common, "state": "grey",
                                   "why": (f"no settled unit receipt for subject "
                                           f"{subject!r}: "
                                           + ("geo_unit_receipts holds no row under it"
                                              if rset is None else
                                              f"its newest set ({rset['measured_at']}) "
                                              f"settles no tag ({rset['verdicts']})")),
                                   "receipt_ids": (rset or {}).get("receipt_ids", [])})
                continue
            if not rows:
                series_out.append({**common, "state": "grey",
                                   "why": (f"no banked rows in the window "
                                           f"{start.isoformat()} -> {end.isoformat()}"),
                                   "receipt_ids": rset["receipt_ids"]})
                continue
            tags = {r["unit_tag"] for r in rows}
            bad = tags - rset["tags"]
            if bad:
                series_out.append({**common, "state": "grey",
                                   "why": (f"the rows were served in "
                                           f"{sorted(bad)} , which subject {subject!r}'s "
                                           f"newest set does not settle "
                                           f"({sorted(rset['tags'])}); its receipt must be "
                                           f"re-taken before it renders (D-08-02-R)"),
                                   "receipt_ids": rset["receipt_ids"]})
                continue
            series_out.append({
                **common, "state": "served",
                "unit_tag": sorted(tags)[0],
                "unit_settled": (rset["unit_settled"][0] if rset["unit_settled"] else None),
                "receipt_ids": rset["receipt_ids"],
                "receipt_measured_at": rset["measured_at"],
                "n": len(rows),
                "values": {r["ts"].isoformat() if hasattr(r["ts"], "isoformat")
                           else str(r["ts"]): (float(r["value"])
                                               if r["value"] is not None else None)
                           for r in rows},
            })
        stations.append({"source_key": loc, "kind": rec["kind"], "name": rec["name"],
                         "record_sha256": rec["record_sha256"], "series": series_out})

    drawable = bool(gate.get("drawable"))
    nwm = {
        "drawn": False,
        "why_not_drawn": (None if drawable else
                          "the gate does not read drawable (D-09-25-232 §3)"),
        "gate_line": gate.get("line"),
        "no_da_line": gate.get("no_da_line"),
        "velocity": {"absence": "never_served",
                     "why": "NWM velocity is never served (D-09-25-237)"},
    }
    if xw.get("absence"):
        nwm["absence"], nwm["why"] = "no_crosswalk", xw["why"]
    else:
        nwm["absence"] = "in_crosswalk_object"
        nwm["why"] = (f"each placed reach's feature_id is in {xw['layer_code']}'s "
                      f"places[].feature_id and reaches[].feature_id; this route reads no "
                      f"R2, and the gate does not read drawable, so nothing would be drawn "
                      f"from them today")
        nwm["crosswalk"] = {"url": xw["url"], "sha256": xw["sha256"]}
    return {
        "river": river, "walk": layer_code,
        "from": start.isoformat(), "to": end.isoformat(),
        "window_days": round((end - start).total_seconds() / 86400.0, 4),
        "max_window_days": MAX_WINDOW_DAYS,
        "count": len(stations), "stations": stations,
        "nwm": nwm, "gate": gate,
        "rule": ("a series is served only when the subject's newest receipt set settles "
                 "the tag the rows were served in (D-09-25-232 §2); the subject carries "
                 "the version and the relay basin (geo/cwms.scope_subject)"),
        "reads_no_r2": True,
    }


# ═══════════════════════════════════════════════════ /gate ════════════════════

GATE_SUBJECT_PREFIX = "nwm_reach:"
GATE_ARBITER = {"Q4": ("cwms_outflow_hourly",
                       "no banked release (cwms_outflow_hourly holds no series for it)"),
                "Q3": ("usgs_iv_discharge",
                       "no banked discharge (usgs_iv_discharge holds no series for it)")}
NO_GATE_RECEIPT_WHY = ("no banked nwm_reach receipt (the gate has run in plan mode only; "
                       "scripts/nwm_reach_gate.py banks receipts, and the drawable line is "
                       "computed from them)")


def receipt_from_row(row: dict) -> dict:
    """A banked `geo_unit_receipts` row in the shape `line_of`/`gate_line` read.

    `_detail` is EMPTY: the table banks no _detail column, so a line recomputed from the
    bank carries no 'hour pairs' clause. The pantry's own `receipt ...` stdout lines are
    this same shape; its reach lines have the clause because they are computed in-process."""
    stat = row.get("statistic")
    return {"verdict": row["verdict"],
            "statistic": (float(stat) if stat is not None else None),
            "n_obs": row.get("n_obs"),
            "subject": row["subject"], "receipt_id": row.get("receipt_id"),
            "unit_tag": row.get("unit_tag"), "unit_settled": row.get("unit_settled"),
            "measured_at": row.get("measured_at"),
            "_detail": {}}


def gate_items(q: str, places: list, xw_layer: str, by_subject: dict,
               arbiter_present: set, *, no_da: bool) -> list:
    """`[(q, name, receipt | None, why)]` in the reach's order — the gate's own rule
    (scripts/nwm_reach_gate.compute)."""
    dataset, missing_why = GATE_ARBITER[q]
    items = []
    for rec in places:
        name = rec["source_key"]
        if (dataset, name) not in arbiter_present:
            items.append((q, name, None, missing_why))
            continue
        subject = f"{GATE_SUBJECT_PREFIX}{xw_layer}:{name}" + (":no_da" if no_da else "")
        row = by_subject.get(subject)
        items.append((q, name, receipt_from_row(row) if row else None,
                      None if row else NO_GATE_RECEIPT_WHY))
    return items


def anchor_km_lo(rec: dict):
    km = ((bank_of(rec).get("anchor") or {}).get("km_from_reach_start"))
    if isinstance(km, list) and km:
        return float(km[0])
    if isinstance(km, (int, float)):
        return float(km)
    return None


def build_gate(river: str, layer_code: str, walk_rows: list, record_rows: list,
               gate_receipt_rows: list, arbiter_present: set,
               cycle_rows: Optional[list] = None) -> dict:
    """The gate as a set of receipts, recomputed by the ported `gate_line`/`line_of`
    (D-09-25-237). `forecast` is served ONLY when the line reads drawable.

    A river without a crosswalk reads `no_crosswalk`: the gate's subjects are keyed on the
    crosswalk's layer_code, and its items are its places (D-09-25-247 §3)."""
    ledger = shape_ledger(walk_rows, record_rows)
    xw = crosswalk_served(ledger["crosswalks"].get(layer_code))
    recs = ledger["records"].get(layer_code, [])
    rows = [r for r in (gate_receipt_rows or [])
            if (r.get("subject") or "").startswith(GATE_SUBJECT_PREFIX)]

    if xw.get("absence"):
        return {"river": river, "walk": layer_code,
                "absence": "no_crosswalk", "why": xw["why"],
                "line": None, "drawable": False, "decided_by": [],
                "banked": len(rows), "no_da_line": None, "forecast": None,
                "crosswalk": xw, "reads_no_r2": True}

    xw_layer = xw["layer_code"]
    mine = [r for r in rows if (r["subject"] or "").startswith(
        f"{GATE_SUBJECT_PREFIX}{xw_layer}:")]
    by_subject = {r["subject"]: r for r in mine}

    dams = [r for r in recs if r["kind"] == "cwms_project"]
    gauges = [r for r in recs if r["kind"] == "gauge"]
    q, places = ("Q4", dams) if dams else ("Q3", gauges)
    if q == "Q4":
        places = sorted(places, key=lambda r: (anchor_km_lo(r) is None,
                                               anchor_km_lo(r) or 0.0, r["source_key"]))
        order_by, order_absence = "the ledger's own anchor km (attrs._bank.anchor" \
                                  ".km_from_reach_start[0])", None
    else:
        places = sorted(places, key=lambda r: r["source_key"])
        order_by = "the records' source_key"
        order_absence = {
            "absence": "in_crosswalk_object",
            "why": (f"the gate orders its items by the crosswalk's places[].km, which is in "
                    f"{xw_layer} and not in Neon; a gauge record banks no anchor, so this "
                    f"route orders by source_key. The ORDER decides only WHICH item is "
                    f"named as deciding, never `drawable`.")}

    items_da = gate_items(q, places, xw_layer, by_subject, arbiter_present, no_da=False)
    items_no_da = gate_items(q, places, xw_layer, by_subject, arbiter_present, no_da=True)
    line, ok = gate_line(layer_code, items_da)
    line2, ok2 = gate_line(layer_code, items_no_da)

    forecast = None
    forecast_absence = None
    if ok:
        forecast = [{"series": c["series"], "from": c["first_ts"], "to": c["last_ts"],
                     "n": c["n"]} for c in (cycle_rows or [])]
        if not forecast:
            forecast_absence = {
                "absence": "in_crosswalk_object",
                "why": (f"the forecast's series are <feature_id>/<product>/<cycle>, and the "
                        f"feature_ids are in {xw_layer}; this route reads no R2")}
    else:
        forecast_absence = {"absence": "gate_not_drawable", "why": line}

    return {
        "river": river, "walk": layer_code, "crosswalk": xw,
        "line": line, "drawable": ok,
        "no_da_line": "_no_da " + line2, "no_da_drawable": ok2,
        "decided_by": [line_of(*i) for i in items_da],
        "no_da_decided_by": [line_of(*i) for i in items_no_da],
        "banked": len(mine),
        "banked_all_rivers": len(rows),
        "question": q,
        "items": [{"q": i[0], "name": i[1], "has_receipt": i[2] is not None,
                   "why": i[3]} for i in items_da],
        "order_by": order_by, "order_absence": order_absence,
        "forecast": forecast, "forecast_absence": forecast_absence,
        "rule": ("drawable only when every item's newest receipt is settled; else the first "
                 "that is not decides (scripts/nwm_reach_gate.gate_line, ported byte for "
                 f"byte from pantry {PANTRY_COMMIT[:7]}); n >= {MIN_DAYS}, tolerance {TOL}"),
        "hourly_pairs_absence": {"absence": "not_banked", "why": HOURLY_PAIRS_WHY},
        "ported_from": {"commit": PANTRY_COMMIT,
                        "functions": ["gate_line", "line_of"],
                        "min_days": MIN_DAYS, "tolerance": TOL},
        "reads_no_r2": True,
    }


# ═══════════════════════════════════════════════════ /reaches ═════════════════

def build_reaches(river: str, layer_code: str, walk_rows: list, record_rows: list,
                  gate: dict, value_rows: Optional[list] = None, *,
                  start: datetime, end: datetime) -> dict:
    """Every crosswalked reach's analysis `streamflow` per hour, served ONLY when the gate
    reads drawable; otherwise `{absence: 'gate_not_drawable', line}` (D-09-25-232 §3).
    NEVER velocity (D-09-25-237)."""
    ledger = shape_ledger(walk_rows, record_rows)
    xw = crosswalk_served(ledger["crosswalks"].get(layer_code))
    head = {"river": river, "walk": layer_code,
            "from": start.isoformat(), "to": end.isoformat(),
            "dataset": "nwm_reach_analysis_hourly", "parameter": "streamflow",
            "velocity": {"absence": "never_served",
                         "why": "NWM velocity is never served (D-09-25-237)"},
            "gate": {"line": gate.get("line"), "drawable": bool(gate.get("drawable")),
                     "no_da_line": gate.get("no_da_line"),
                     "banked": gate.get("banked")},
            "reads_no_r2": True}
    if xw.get("absence"):
        return {**head, "absence": "no_crosswalk", "why": xw["why"], "reaches": []}
    if not gate.get("drawable"):
        return {**head, "absence": "gate_not_drawable", "why": gate.get("line"),
                "reaches": []}
    rows: dict = {}
    for row in value_rows or []:
        fid = str(row["series"]).split("/")[0]
        rows.setdefault(fid, {})[row["ts"].isoformat()] = (
            float(row["value"]) if row["value"] is not None else None)
    if not rows:
        return {**head, "absence": "in_crosswalk_object",
                "why": (f"every crosswalked reach's feature_id is in {xw['layer_code']}'s "
                        f"reaches[].feature_id; this route reads no R2, so the series it "
                        f"would name cannot be enumerated here"),
                "crosswalk": {"url": xw["url"], "sha256": xw["sha256"]}, "reaches": []}
    return {**head, "count": len(rows),
            "reaches": [{"fid": fid, "series": f"{fid}/streamflow", "n": len(v),
                         "values": v} for fid, v in sorted(rows.items())]}
