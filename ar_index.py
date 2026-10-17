"""The atmospheric-river index — d091766 (ruling D-09-25-245, show_v1: migration 292).

    GET /api/weather/ar/index          the AR bank's published objects per banked cycle, the
                                       registry, and the show_v1 census of the 50 cells
    GET /api/weather/ar/landfall?init= per coast point x lead: the model IVT quantities, and a
                                       probability and its counts only where the point's cell
                                       shows; everything else withheld with its reason

READ-ONLY, and NO R2. The API serves what the AR bank publishes (pantry scripts/ar_publish.py,
migration 324's ar_display_objects: the display bucket's key, sha and size) and the newest
cycle's odds rows (292's ar_landfall_latest), and gates every probability by its scored cell
(292's ar_scores). The browser fetches the objects from the display bucket. This module holds
the SQL, the edges and the shaping; main.py holds the routes, the memos and the statement
timeout (D-09-25-75), as for the Tropics index (tropics_index.py).

THE GATE (D-09-25-245). A cell is (region, lead bucket, threshold): 5 x 5 x 2 = 50. A "k of N
members", a percentage or an odds value (n250/p250, n500/p500) is served for a (point, lead)
only where the newest scoring's k_bin = 'all' row for that point's cell says show = true. Every
other threshold of every (point, lead) is withheld with its reason, and carries no probability
and no count. ens_mean, ctrl and max_member are model IVT quantities and are always served
(D-09-25-245 §2), as are n_present and absent_members (members present, not an exceedance).
The counts sit non-null on every banked row (ruling 6), so the gate is applied on the way out,
per cell and per threshold; nothing is inferred from a null.

SHOW_V1 AS THE SCHEMA HOLDS IT (ruling 3). S1 (n_eff >= 60, event_days_eff >= 14) and S2
(bss_lo90 > 0) are terms of 292's ar_scores_show_rule_v1 CHECK; S4 (like-for-like truth) is its
own CHECK, ar_scores_like_for_like; S3 (calibration) is the scorer's, and reaches Neon only
through show_reasons, which the show_v1 CHECK requires to be empty where show = true. This
module serves show and show_reasons as written and recomputes no S.

THE EDGES (ruling 1). EDGES is the pantry's ar.cells.edges() copied, not imported, and its
fingerprint is pinned to the one `scripts/ar_cells.py --edges` printed (EDGES_PIN). The sha is
recomputed here; if it ever differs from the pin, no cell is served (every threshold withheld
edges_pin_mismatch) and the index says so. Region and lead bucket are derived by the pinned
rules (ruling 2): region is the band whose [lat_min, lat_max] holds lat (the half-degree
between bands belongs to the band below), once per point; bucket is gt_h < lead_h <= le_h. A
(point, lead) in no cell (today: lead 0, 71 rows) is withheld no_cell with the edges' own rule,
verbatim (ruling 4). The published coast_ivt object states each point's region; the suite
cross-checks the derivation against it (A4b). The route reads no object, so in the route a
point whose rows disagree with themselves or with their point_code is a 500 naming the point,
never a silent pick.

THE READS (D-09-25-75; CLAUDE.md d091551; D-08-02-L). Each names one level set. The cycle's
objects are one LATERAL per (cycle, kind), each an exact primary-key probe of
ar_display_objects at the key 324's ar_display_cycle_key CHECK makes the object's; the newest
scoring is one backwards range read, LIMIT 1, then its 'all' rows by that scored_at. No
DISTINCT ON. Receipts: docs/receipts/ar-index-d091766/.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

# ── the pinned edges (ruling 1) ─────────────────────────────────────────────

# energylake-pantry ar/cells.py edges(), copied verbatim (ar/points.py REGIONS, ar/score.py
# LEAD_BUCKETS / THRESHOLDS / K_BINS / SHOW_RULE["version"]).
EDGES = {
    "schema": "ar.cells.edges.v1",
    "show_rule": "show_v1",
    "regions": [{"name": "baja_socal", "lat_min": 25.0, "lat_max": 34.5},
                {"name": "central_norcal", "lat_min": 35.0, "lat_max": 42.5},
                {"name": "or_wa", "lat_min": 43.0, "lat_max": 49.5},
                {"name": "bc", "lat_min": 50.0, "lat_max": 55.5},
                {"name": "se_alaska", "lat_min": 56.0, "lat_max": 60.0}],
    "region_rule": "a coast point belongs to the region whose [lat_min, lat_max] holds its "
                   "latitude (0.5-deg coast list: the half-degrees between regions sit in "
                   "the region below)",
    "lead_buckets": [{"name": "d1-2", "gt_h": 0, "le_h": 48},
                     {"name": "d3-5", "gt_h": 48, "le_h": 120},
                     {"name": "d6-8", "gt_h": 120, "le_h": 192},
                     {"name": "d9-12", "gt_h": 192, "le_h": 288},
                     {"name": "d13-16", "gt_h": 288, "le_h": 384}],
    "lead_rule": "lead_h = valid_ts - init_ts in hours; bucket where gt_h < lead_h <= le_h; "
                 "lead 0 is in no bucket and never shows",
    "thresholds": [250, 500],
    "exceedance": ">=",
    "k_bins": [{"name": "0", "k31_min": 0, "k31_max": 0},
               {"name": "1-3", "k31_min": 1, "k31_max": 3},
               {"name": "4-9", "k31_min": 4, "k31_max": 9},
               {"name": "10-15", "k31_min": 10, "k31_max": 15},
               {"name": "16-21", "k31_min": 16, "k31_max": 21},
               {"name": "22-27", "k31_min": 22, "k31_max": 27},
               {"name": "28-31", "k31_min": 28, "k31_max": 31}],
    "k_rule": "k31 = round(k * 31 / n_present)",
}
EDGES_PIN = {
    "edges_sha256": "5b0068645d1c3514365f50f15a6900c15bdd6758cde6c1580c2e82ccfb6cc0fe",
    "source": "energylake-pantry scripts/ar_cells.py --edges at "
              "af0aeb902b637218f434110722cdf12170618ee5 (edge files last changed b786fe79)",
}


def edges_sha256(edges: Optional[dict] = None) -> str:
    """ar.cells.edges_sha256(): sorted keys, no whitespace. Of EDGES as it stands now."""
    return hashlib.sha256(json.dumps(EDGES if edges is None else edges, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def edges_pinned() -> bool:
    return edges_sha256() == EDGES_PIN["edges_sha256"]


REGION_NAMES = tuple(r["name"] for r in EDGES["regions"])
BUCKET_NAMES = tuple(b["name"] for b in EDGES["lead_buckets"])
THRESHOLDS = tuple(EDGES["thresholds"])


def region_of(lat) -> Optional[str]:
    """The band whose [lat_min, lat_max] holds lat, or None. Bands are disjoint; two that
    both hold a latitude would be a broken pin, and are refused."""
    lat = Decimal(str(lat))
    hit = [r["name"] for r in EDGES["regions"]
           if Decimal(str(r["lat_min"])) <= lat <= Decimal(str(r["lat_max"]))]
    if len(hit) > 1:
        raise RuntimeError(f"latitude {lat} is in two regions {hit}: the edges overlap")
    return hit[0] if hit else None


def bucket_of(lead_h: int) -> Optional[str]:
    """The bucket where gt_h < lead_h <= le_h, or None (lead 0 is in none)."""
    for b in EDGES["lead_buckets"]:
        if b["gt_h"] < lead_h <= b["le_h"]:
            return b["name"]
    return None


def universe() -> list[tuple[str, str, int]]:
    return [(r, b, t) for r in REGION_NAMES for b in BUCKET_NAMES for t in THRESHOLDS]


# ── the bank's contract (pantry ar/publish.py, migrations 292 and 324) ──────

LEVEL_SET = "sub7_v1"          # the bank's one level set (292's datasets config; ar_runs)
DISPLAY_BASE = "https://display.energylake.io/"
# pantry ar/publish.py BIAS and ATTRIBUTION, verbatim (the coast_ivt object carries both).
BIAS_NOTE = ("7-level IVT (1000–300 hPa); reads ~40–80 kg m⁻¹ s⁻¹ low at landfall "
             "against a full column")
ATTRIBUTION = "NOAA GEFS (NODD). Computed by EnergyLake; not endorsed by NOAA."
UNITS = "kg m-1 s-1"
POINT_CODE = re.compile(r"^coast_([0-9]{2}\.[05])N$")     # 292's point_code CHECK


def display_url(key: Optional[str]) -> Optional[str]:
    return None if key is None else DISPLAY_BASE + key


def parse_init(raw: Optional[str]) -> Optional[datetime]:
    """A cycle as YYYYMMDDHH (the bank's spelling) or ISO 8601 UTC; 00Z or 12Z (292's
    ar_runs_cycle_00_12). None: the newest held."""
    if raw is None or raw == "":
        return None
    if re.match(r"^[0-9]{10}$", raw):
        t = datetime.strptime(raw, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    else:
        try:
            t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"init must be a cycle as YYYYMMDDHH (e.g. 2026101700) or ISO 8601 "
                             f"UTC (e.g. 2026-10-17T00:00:00Z), got {raw!r}") from None
        if t.tzinfo is None:
            raise ValueError(f"init {raw!r} names no zone: give it in UTC (Z or +00:00)")
        t = t.astimezone(timezone.utc)
    if (t.minute, t.second, t.microsecond) != (0, 0, 0) or t.hour not in (0, 12):
        raise ValueError(f"init {raw!r} is not a 00Z or 12Z cycle: the AR bank pulls GEFS at "
                         f"00Z and 12Z only")
    return t


def _ts(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v.astimezone(timezone.utc).isoformat()


def _num(v):
    """Neon's numeric/real as JSON numbers (lat/lon numeric(4,1)/(5,1); IVT real)."""
    if v is None:
        return None
    return float(v) if isinstance(v, (Decimal, float)) else v


# ── statements ──────────────────────────────────────────────────────────────

# A cycle object's key, as 324's ar_display_cycle_key CHECK makes it: so each LATERAL below
# is one exact probe of ar_display_objects' primary key (level_set, kind, display_key).
_CYCLE_KEY = ("'ar/gefs/' || to_char(r.init_ts AT TIME ZONE 'UTC', 'YYYYMMDD') || '/' "
              "|| to_char(r.init_ts AT TIME ZONE 'UTC', 'HH24') || 'Z/' || {kind} || '.' "
              "|| r.level_set || '.json'")

# Every banked cycle of the level set (ar_runs: 16 rows, two a day), newest first, each with
# its published coast_ivt object and whether its odds object is published: one LATERAL per
# (cycle, kind), each one primary-key probe. LIMIT 1 keeps each LATERAL a subquery run per
# cycle (CLAUDE.md d091551's shape): without it Postgres pulls the subquery up into a join,
# walks the (level_set, kind) range per cycle and drops the other keys by Join Filter (Neon
# 2026-10-17 12:58Z: 8 rows walked per cycle, 120 removed per kind).
INDEX_SQL = f"""
    SELECT r.init_ts, r.level_set, r.status, r.members_present, r.steps_with_odds,
           r.method_hash, r.written_at,
           c.display_key AS coast_key, c.sha256 AS coast_sha256, c.bytes AS coast_bytes,
           c.published_at AS coast_published_at,
           o.display_key IS NOT NULL AS odds_published
      FROM ar_runs AS r
      LEFT JOIN LATERAL (
            SELECT d.display_key, d.sha256, d.bytes, d.published_at
              FROM ar_display_objects AS d
             WHERE d.level_set = r.level_set AND d.kind = 'coast_ivt'
               AND d.display_key = {_CYCLE_KEY.format(kind="'coast_ivt'")}
             LIMIT 1) AS c ON true
      LEFT JOIN LATERAL (
            SELECT d.display_key
              FROM ar_display_objects AS d
             WHERE d.level_set = r.level_set AND d.kind = 'odds'
               AND d.display_key = {_CYCLE_KEY.format(kind="'odds'")}
             LIMIT 1) AS o ON true
     WHERE r.level_set = %(level_set)s
     ORDER BY r.init_ts DESC
"""

# The newest published registry of the level set: one (level_set, kind) range of the
# primary key, newest publish first, LIMIT 1.
REGISTRY_SQL = """
    SELECT d.display_key, d.sha256, d.bytes, d.published_at
      FROM ar_display_objects AS d
     WHERE d.level_set = %(level_set)s AND d.kind = 'registry'
     ORDER BY d.published_at DESC, d.display_key DESC
     LIMIT 1
"""

# The newest scoring of the level set (a backwards read of the primary key's scored_at,
# LIMIT 1), then its k_bin 'all' rows at that scored_at: show and show_reasons as written.
SCORES_SQL = """
    SELECT s.scored_at, s.scorer_hash, s.show_rule, s.region, s.lead_bucket, s.threshold,
           s.show, s.show_reasons
      FROM (SELECT a.scored_at
              FROM ar_scores AS a
             WHERE a.level_set = %(level_set)s
             ORDER BY a.scored_at DESC
             LIMIT 1) AS n
      JOIN LATERAL (
            SELECT b.scored_at, b.scorer_hash, b.show_rule, b.region, b.lead_bucket,
                   b.threshold, b.show, b.show_reasons
              FROM ar_scores AS b
             WHERE b.scored_at = n.scored_at AND b.level_set = %(level_set)s
               AND b.k_bin = 'all') AS s ON true
     ORDER BY s.region, s.lead_bucket, s.threshold
"""

# The cycle ar_landfall_latest holds (the newest only, replaced per cycle): its first row by
# the primary key's (level_set) prefix, LIMIT 1.
LANDFALL_INIT_SQL = """
    SELECT l.init_ts
      FROM ar_landfall_latest AS l
     WHERE l.level_set = %(level_set)s
     ORDER BY l.point_code, l.valid_ts
     LIMIT 1
"""

# Every (point, lead) of that cycle, by the primary key's order. The whole table is one cycle
# (71 points x 93 steps = 6,603 rows), all of which the route serves.
LANDFALL_SQL = """
    SELECT l.init_ts, l.valid_ts, l.lead_h, l.point_code, l.lat, l.lon, l.n250, l.n500,
           l.n_present, l.ens_mean, l.ctrl, l.max_member, l.absent_members,
           l.tc_named_system, l.tc_rule_version
      FROM ar_landfall_latest AS l
     WHERE l.level_set = %(level_set)s AND l.init_ts = %(init_ts)s
     ORDER BY l.point_code, l.valid_ts
"""


def params(**kw) -> dict:
    return {"level_set": LEVEL_SET, **kw}


# ── the census (ar_scores, served as written) ───────────────────────────────

REASONS = {
    "no_init": "the bank holds no cycle to serve (ar_runs, or ar_landfall_latest for /landfall)",
    "not_published": "the cycle is banked (ar_runs); ar_display_objects holds no published object "
                     "of this kind for it",
    "not_yet_scored": "the newest scoring in ar_scores holds no k_bin 'all' row for this "
                      "(region, lead bucket, threshold) cell",
    "not_shown": "the cell is scored and its 'all' row says show = false; show_reasons are the "
                 "scorer's, as written",
    "no_cell": "the (point, lead) is in no cell of the pinned edges: rule is the edges' own",
    "edges_pin_mismatch": "the edges constant's sha is not the pinned one, so no cell is served",
}


def census(score_rows: list) -> dict:
    """{(region, bucket, threshold): row} of the newest scoring's 'all' rows. Two rows for
    one cell, or a cell outside the universe, is refused (pantry ar.cells.census)."""
    by = {}
    cells = set(universe())
    for r in score_rows:
        key = (r["region"], r["lead_bucket"], int(r["threshold"]))
        if key in by:
            raise RuntimeError(f"two k_bin 'all' rows for cell {key} in one scoring")
        if key not in cells:
            raise RuntimeError(f"an ar_scores row for cell {key}, outside the 50-cell universe")
        by[key] = r
    return by


def _cell_verdict(row: Optional[dict]) -> dict:
    """One cell as ar_scores states it: show / show_reasons as written, or not_yet_scored."""
    if row is None:
        return {"scored": False, "show": None, "show_reasons": None,
                "withheld": {"reason": "not_yet_scored"}}
    shown = row["show"] is True
    return {"scored": True, "show": row["show"], "show_reasons": list(row["show_reasons"] or []),
            "withheld": None if shown else {"reason": "not_shown",
                                            "show_reasons": list(row["show_reasons"] or [])}}


def scoring_block(score_rows: list) -> dict:
    by = census(score_rows)
    pinned = edges_pinned()
    cells = []
    for region, bucket, thr in universe():
        v = _cell_verdict(by.get((region, bucket, thr)))
        if not pinned:
            v["withheld"] = {"reason": "edges_pin_mismatch"}
        cells.append({"region": region, "lead_bucket": bucket, "threshold": thr, **v})
    head = score_rows[0] if score_rows else None
    return {
        "rule": EDGES["show_rule"],
        "cells_scored": sum(c["scored"] for c in cells),
        "cells_shown": sum(1 for c in cells if c["withheld"] is None),
        "cells_total": len(cells),
        "scored_at": _ts(head["scored_at"]) if head else None,
        "scorer_hash": head["scorer_hash"] if head else None,
        "cells": cells,
        "show_rule": SHOW_RULE_TEXT,
    }


SHOW_RULE_TEXT = (
    "show_v1 as migration 292 holds it: S1 (n_eff >= 60, event_days_eff >= 14) and S2 "
    "(bss_lo90 > 0) are terms of the ar_scores_show_rule_v1 CHECK, which also requires "
    "show_rule = 'show_v1' and an empty show_reasons where show = true; S4 (truth_level_set = "
    "level_set) is its own CHECK, ar_scores_like_for_like; S3 (calibration) is the scorer's "
    "and reaches Neon only as a show_reasons entry. The API serves show and show_reasons as "
    "written by the newest scoring (max scored_at, k_bin 'all') and recomputes no S. A cell "
    "with no row is not_yet_scored.")


# ── the edges block both routes print ───────────────────────────────────────

def edges_block() -> dict:
    sha = edges_sha256()
    return {"edges_sha256": sha, "pin": EDGES_PIN["edges_sha256"], "pinned": sha == EDGES_PIN["edges_sha256"],
            "source": EDGES_PIN["source"], "regions": EDGES["regions"],
            "lead_buckets": EDGES["lead_buckets"], "thresholds": list(THRESHOLDS),
            "exceedance": EDGES["exceedance"], "region_rule": EDGES["region_rule"],
            "lead_rule": EDGES["lead_rule"]}


# ── /index ──────────────────────────────────────────────────────────────────

INDEX_RULE = ("Each object is a row of migration 324's ar_display_objects, the publish ledger of "
              "pantry scripts/ar_publish.py: url = " + DISPLAY_BASE + "<display_key>, sha256 and "
              "bytes of the published bytes. The API reads no R2 and fetches no object. An init "
              "is a banked cycle (292's ar_runs); odds_published says whether the cycle's odds "
              "object is in the ledger, whose url is not served (a bank artifact, never drawn "
              "as-is: D-09-25-245).")
NOT_SERVED = {
    "odds_object": ("The cycle's odds object (odds.<level_set>.json) carries every count of every "
                    "(point, lead) ungated: it is a bank artifact the API gates per cell and never "
                    "serves, so its url is not given. /landfall serves its counts where a cell "
                    "shows."),
    "object_bytes": ("This API reads no R2 and serves no object bytes: every url is the display "
                     "bucket's, and the browser fetches it."),
}


def build_index(*, runs: list, registry: Optional[dict], score_rows: list) -> dict:
    """GET /index. `runs` are INDEX_SQL's rows (newest first), `registry` REGISTRY_SQL's row."""
    inits = []
    for r in runs:
        if r["coast_key"] is not None:
            coast = {"url": display_url(r["coast_key"]), "sha256": r["coast_sha256"],
                     "bytes": r["coast_bytes"], "published_at": _ts(r["coast_published_at"]),
                     "absence": None}
        else:
            coast = {"url": None, "sha256": None, "bytes": None, "published_at": None,
                     "absence": {"reason": "not_published",
                                 "detail": f"cycle {_ts(r['init_ts'])} is banked (ar_runs, status "
                                           f"{r['status']}); ar_display_objects holds no "
                                           f"coast_ivt object for it"}}
        inits.append({"init_ts": _ts(r["init_ts"]), "status": r["status"],
                      "members_present": r["members_present"],
                      "steps_with_odds": r["steps_with_odds"], "method_hash": r["method_hash"],
                      "coast_ivt": coast, "odds_published": bool(r["odds_published"])})
    reg = ({"url": display_url(registry["display_key"]), "sha256": registry["sha256"],
            "bytes": registry["bytes"], "published_at": _ts(registry["published_at"]),
            "absence": None} if registry is not None else
           {"url": None, "sha256": None, "bytes": None, "published_at": None,
            "absence": {"reason": "not_published",
                        "detail": f"ar_display_objects holds no registry object for level set "
                                  f"{LEVEL_SET}"}})
    eb = edges_block()
    return {
        "level_set": LEVEL_SET,
        "newest_init": inits[0]["init_ts"] if inits else None,
        "inits_published": sum(1 for i in inits if i["coast_ivt"]["absence"] is None),
        "inits": inits,
        "registry": reg,
        "scoring": scoring_block(score_rows),
        "edges_sha256": eb["edges_sha256"],
        "edges": eb,
        "bias_note": BIAS_NOTE,
        "attribution": ATTRIBUTION,
        "absence": None if inits else {"reason": "no_init",
                                       "detail": f"ar_runs holds no cycle of level set {LEVEL_SET}"},
        "order": "newest init first",
        "index_rule": INDEX_RULE, "reasons": REASONS, "not_served": NOT_SERVED,
        "timestamps": "ISO 8601, UTC, e.g. 2026-10-17T00:00:00+00:00",
    }


# ── /landfall ───────────────────────────────────────────────────────────────

class PointMismatch(RuntimeError):
    """A point whose rows disagree with themselves or their point_code: the route's 500."""

    def __init__(self, point_code: str, detail: str):
        super().__init__(f"{point_code}: {detail}")
        self.point_code = point_code


def points_of(rows: list) -> dict:
    """{point_code: (lat, lon, region)}, derived ONCE per point (ruling 2). Every row of a
    point must carry the point's one lat/lon, and the lat its point_code names."""
    out = {}
    for r in rows:
        pc = r["point_code"]
        lat, lon = Decimal(str(r["lat"])), Decimal(str(r["lon"]))
        if pc not in out:
            m = POINT_CODE.match(pc)
            if m is None or Decimal(m.group(1)) != lat:
                raise PointMismatch(pc, f"point_code names latitude "
                                        f"{m.group(1) if m else '?'} but the row's lat is {lat}")
            out[pc] = (lat, lon, region_of(lat))
        elif out[pc][:2] != (lat, lon):
            raise PointMismatch(pc, f"rows carry two positions, {out[pc][:2]} and {(lat, lon)}: "
                                    f"one point gets one region")
    return out


LANDFALL_RULE = (
    "One row per (point, lead) of the cycle ar_landfall_latest holds (the newest only). ens_mean, "
    "ctrl and max_member are model IVT in kg m-1 s-1 and are always served (D-09-25-245 §2), as "
    "are n_present and absent_members. p250/n250 and p500/n500 (p = n / n_present, members >= the "
    "threshold) are served ONLY where the row's (region, lead_bucket, threshold) cell shows: the "
    "newest scoring's k_bin 'all' row says show = true. Each threshold not served is a key of "
    "`withheld` with its reason (not_yet_scored | not_shown with the scorer's show_reasons | "
    "no_cell with the edges' rule verbatim | edges_pin_mismatch), and that threshold's "
    "probability and count are absent from the row, not null. region is derived once per point "
    "by the pinned region_rule, lead_bucket by the pinned lead_rule.")


def _withheld_for(region, bucket, thr, by, pinned) -> Optional[dict]:
    if not pinned:
        return {"reason": "edges_pin_mismatch"}
    if region is None:
        return {"reason": "no_cell", "rule": EDGES["region_rule"]}
    if bucket is None:
        return {"reason": "no_cell", "rule": EDGES["lead_rule"]}
    return _cell_verdict(by.get((region, bucket, thr)))["withheld"]


def build_landfall(*, init: Optional[datetime], held: Optional[datetime], rows: list,
                   score_rows: list) -> dict:
    """GET /landfall. `held` is the cycle ar_landfall_latest holds, `init` the one asked
    (None: the held one); `rows` LANDFALL_SQL's rows at `held`."""
    eb = edges_block()
    head = {"level_set": LEVEL_SET, "init_ts": _ts(held) if held else None,
            "init_requested": _ts(init) if init else None, "units": UNITS}
    tail = {"edges_sha256": eb["edges_sha256"], "edges": eb, "landfall_rule": LANDFALL_RULE,
            "show_rule": SHOW_RULE_TEXT, "reasons": REASONS, "bias_note": BIAS_NOTE,
            "attribution": ATTRIBUTION,
            "timestamps": "ISO 8601, UTC, e.g. 2026-10-17T00:00:00+00:00"}
    if held is None or (init is not None and init != held):
        detail = (f"ar_landfall_latest holds no cycle of level set {LEVEL_SET}" if held is None else
                  f"ar_landfall_latest holds the newest cycle only, {_ts(held)}; {_ts(init)} is "
                  f"not held")
        return {**head, "rows": [], "counts": None, "scoring": scoring_block(score_rows),
                "absence": {"reason": "no_init", "detail": detail}, **tail}
    by = census(score_rows)
    pinned = edges_pinned()
    pts = points_of(rows)
    out, n_served, n_withheld = [], {t: 0 for t in THRESHOLDS}, {}
    for r in rows:
        lat, lon, region = pts[r["point_code"]]
        bucket = bucket_of(int(r["lead_h"]))
        row = {"point_code": r["point_code"], "lat": float(lat), "lon": float(lon),
               "region": region, "valid_ts": _ts(r["valid_ts"]), "lead_h": int(r["lead_h"]),
               "lead_bucket": bucket, "n_present": r["n_present"],
               "ens_mean": _num(r["ens_mean"]), "ctrl": _num(r["ctrl"]),
               "max_member": _num(r["max_member"]),
               "absent_members": list(r["absent_members"] or [])}
        withheld = {}
        for thr in THRESHOLDS:
            w = _withheld_for(region, bucket, thr, by, pinned)
            if w is None:
                k = r[f"n{thr}"]
                row[f"p{thr}"] = round(k / r["n_present"], 4)
                row[f"n{thr}"] = k
                n_served[thr] += 1
            else:
                withheld[str(thr)] = w
                n_withheld[w["reason"]] = n_withheld.get(w["reason"], 0) + 1
        row["withheld"] = withheld or None
        out.append(row)
    return {**head, "rows": out,
            "counts": {"rows": len(out), "points": len(pts),
                       "served": {str(t): n for t, n in n_served.items()},
                       "withheld_by_reason": dict(sorted(n_withheld.items()))},
            "scoring": scoring_block(score_rows), "absence": None,
            "order": "point_code, then valid_ts", **tail}
