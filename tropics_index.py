"""The Tropics index — d091741 (D-09-25-226, orders 1-4: the API half), reading no R2.

    GET /api/weather/tropics/ground?basin=al|ep|cp
                                        the GOES GeoColor frame GIBS holds now, probed
                                        tile by tile (never GIBS's `default`)
    GET /api/weather/tropics/products?storm_id=&advisory=
                                        the published cone / watch-warning / surge
                                        objects of one advisory, and the WSP of its
                                        synoptic cycle, each stated or absent with a reason
    GET /api/weather/tropics/text?storm_id=&advisory=
                                        the published TCD and TCP parses of one advisory
    GET /api/weather/tropics/members?storm_id=&cycle=
                                        the published ensemble-member manifests, per cycle

READ-ONLY, and NO R2. This API serves an INDEX: what the pantry published to the
display bucket (display.energylake.io), its key, its sha and why something is
absent, read from the vintage ledger in Neon. The browser fetches the objects from
the display bucket. /ground reads no table: it asks GIBS. This module holds the SQL,
the shaping and the probe; main.py holds the routes, the memos and the statement
timeout (D-09-25-75).

THE LEDGER CONTRACT, AND WHERE IT CAME FROM. d091740 (the pantry's publish step)
had not landed when this lane ran and its spec was not readable from here (no branch
in energylake-pantry, nothing in energylake-architect), so the published rows are
read as recon d091737 §7 proposes them (pantry docs/recon_2026_10_15_tropics_v2_layers
_d091737.md): one vintage-ledger row per published object, `product` = 'display:<name>',
`r2_key` = the display key, sha256 and byte_size of the published bytes, `meta` = the
K1 summary plus the source object's key and sha. The two names the recon left open
are pinned here, in ONE place each, for d091740 to confirm or change:

  * DISPLAY_SOURCE = 'parsed'. The ledger's CHECK (migration 291) admits six sources
    and no 'display'; 'parsed' is the bank's source for objects it derives (the
    'tracks:*' parquets), so a display row needs no migration under it.
  * the meta keys each route reads (META_* below).

A display row is read through tropical_file_vintage_in_force (pantry 294): per
(source, product, storm_id, vintage_key), the copy in force, with `revision` and
`corrected` from meta.correction. Every read names its (source, product, storm_id,
vintage_key) and walks tfv_identity (source, product, storm_id, vintage_key).

THE RULINGS, AND WHERE EACH IS HELD.
  D-09-21-05  a count says what it counted: every absence names what was read and
              found missing (`detail`); `not_issued` is said only on NHC's own word
              (its CurrentStorms entry for the advisory names no such product).
  D-09-09-M   a status code is not a verdict: an object is served because the
              ledger holds its row, never because a URL answered; `too_few_members`
              is the publisher's word on its manifest, never the API's count.
  D-09-05-T   the recon's parsed fixtures and banked-object vectors are pinned by
              sha (tests/fixtures/tropics_d091741/).
  D-09-25-226 the arc: GIBS frame, NHC products, words, members; the API reads no R2.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

import tropics as _tr

# ── parameters ──────────────────────────────────────────────────────────────

# The bank's advisory spelling (pantry tropical/nhc.py norm_adv): 'NNN' or 'NNNA'.
# Never normalised here: '32' or '032a' is a bad parameter, not a guess.
ADVISORY = re.compile(r"^[0-9]{3}A?$")
# A synoptic cycle as the bank keys it (keys.tracks_parquet): YYYYMMDDHH, HH 00/06/12/18.
CYCLE = re.compile(r"^20[0-9]{2}(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])(00|06|12|18)$")


def parse_advisory(raw: Optional[str]) -> Optional[str]:
    if raw is None or raw == "":
        return None
    if not ADVISORY.match(raw):
        raise ValueError(f"advisory must be the bank's spelling NNN or NNNA (e.g. 032, 057A), got {raw!r}")
    return raw


def parse_cycle(raw: Optional[str]) -> Optional[str]:
    if raw is None or raw == "":
        return None
    if not CYCLE.match(raw):
        raise ValueError(f"cycle must be YYYYMMDDHH with HH 00, 06, 12 or 18 (e.g. 2026101112), got {raw!r}")
    datetime.strptime(raw, "%Y%m%d%H")          # 2026023112 is refused, not rolled
    return raw


def parse_basin(raw: Optional[str]) -> str:
    if raw is None or raw == "":
        raise ValueError(f"basin is required: one of {sorted(GROUND_LAYERS_BY_BASIN)}")
    if raw not in GROUND_LAYERS_BY_BASIN:
        raise ValueError(f"basin must be one of {sorted(GROUND_LAYERS_BY_BASIN)}, got {raw!r}")
    return raw


# ── the ledger contract (recon d091737 §7; d091740 to confirm) ──────────────

DISPLAY_BASE = "https://display.energylake.io/"
DISPLAY_SOURCE = "parsed"
NHC_SOURCE = "nhc"
DISPLAY_PREFIX = "display:"

# (served name, the banked NHC file it is published from). Keys, recon §7:
#   tropics/{storm}/adv{ADV}/{cone|ww|surge_ww}.geojson
ADVISORY_PRODUCTS = (("cone", "cone_kmz"), ("ww", "ww_kmz"), ("surge_ww", "surge_ww_kml"))
#   tropics/{storm}/adv{ADV}/{tcd|tcp}.json (the products_text parse)
TEXT_PRODUCTS = (("tcd", "tcd"), ("tcp", "tcp"))
#   tropics/wsp/{cycle}/wsp{34|50|64}_{bands|grid}.geojson, basin-wide (storm_id '')
WSP_PRODUCTS = (("wsp34_bands", "wsp_5km_zip"), ("wsp50_bands", "wsp_5km_zip"),
                ("wsp64_bands", "wsp_5km_zip"), ("wsp34_grid", "wsp_halfdeg_zip"),
                ("wsp50_grid", "wsp_halfdeg_zip"), ("wsp64_grid", "wsp_halfdeg_zip"))
#   tropics/{storm}/{cycle}/{ensemble}/manifest.json, from the banked member parquet
ENSEMBLES = (("ecmwf_ens", "tracks:ecmwf_ens"), ("ecmwf_aifs_ens", "tracks:ecmwf_aifs_ens"),
             ("gefs_adeck", "tracks:adeck"))
ENSEMBLE_LABELS = {"ecmwf_ens": "ECMWF ENS", "ecmwf_aifs_ens": "ECMWF AIFS-ENS",
                   "gefs_adeck": "GEFS members (A-deck AP01-AP30)"}
ENSEMBLE_ECMWF = ("ecmwf_ens", "ecmwf_aifs_ens")
# Storm surge watches and warnings are issued for the US Atlantic and Gulf coasts
# only (NHC); an East or Central Pacific storm has none to bank.
SURGE_BASINS = ("al",)

# The meta keys read off a display row (the K1 summary, recon §3 and §7).
META_OBJECT = ("source_r2_key", "source_sha256", "geometry_types", "vertices")
META_TEXT = ("source_r2_key", "source_sha256", "issued_utc", "transmitted_utc", "forecaster",
             "sections")
META_MEMBERS = ("source_r2_key", "source_sha256", "n_members", "min_members", "taus")


def display_product(name: str) -> str:
    return f"{DISPLAY_PREFIX}{name}"


def members_product(ensemble: str) -> str:
    return f"{DISPLAY_PREFIX}members:{ensemble}"


def display_url(r2_key: Optional[str]) -> Optional[str]:
    return None if r2_key is None else DISPLAY_BASE + r2_key


INDEX_RULE = ("Each object is a vintage-ledger row of the display bucket "
              "(tropical_file_vintage_in_force, source '" + DISPLAY_SOURCE + "', product "
              "'display:<name>', r2_key = the display key), served as the ledger states it: "
              "url = " + DISPLAY_BASE + "<r2_key>. The API reads no R2 and fetches no "
              "object. The contract is recon d091737 §7's; d091740's spec was not readable "
              "when this route was written.")
REASONS = {
    "not_issued": "NHC issued none: its CurrentStorms entry for this advisory links no such "
                  "product, or links an earlier advisory's file",
    "basin_has_none": "NHC issues storm surge watches and warnings for the US Atlantic and Gulf "
                      "coasts only",
    "not_banked": "the bank holds no copy of the NHC file this would be published from",
    "not_published": "the NHC file is banked; no published display object is in the ledger",
}
NOT_SERVED_INDEX = {
    "object_bytes": ("This API reads no R2 and serves no object bytes: every url is the display "
                     "bucket's, and the browser fetches it."),
    "alerts": ("NWS tropical watches and warnings (api.weather.gov) have no route here: the page "
               "fetches them directly (recon d091737 §5)."),
}


# ── statements ──────────────────────────────────────────────────────────────

# The default advisory: NHC's newest, by its CurrentStorms entry (issued_ts,
# then the newest fetch). Not the newest official forecast: a post-tropical
# advisory is text only (Isaias 016-020: TCD and TCP, no GIS, no forecast
# points), so the official forecast's newest (015) is not NHC's newest (020).
# One tfv_identity range: the storm's entries (a few hundred at most), sorted.
ENTRY_NEWEST_SQL = """
    SELECT vintage_key, issued_ts, fetch_ts, meta
      FROM tropical_file_vintage_in_force
     WHERE source = 'nhc' AND product = 'storm_entry' AND storm_id = %(storm_id)s
     ORDER BY issued_ts DESC, fetch_ts DESC, vintage_id DESC
     LIMIT 1
"""

# A storm with no CurrentStorms entry falls back to its newest official
# advisory: the newest init (one LIMIT 1 down uniq_tropical_track_points'
# (storm_id, source, init_ts) prefix), then the advisories at that init, newest
# by their own position's valid time (the /storms rule, never the string).
ADVISORY_NEWEST_SQL = """
    SELECT p.advisory, p.init_ts, min(p.valid_ts) AS position_valid_ts
      FROM (SELECT init_ts
              FROM tropical_track_points_in_force
             WHERE storm_id = %(storm_id)s AND source = 'nhc_official'
             ORDER BY init_ts DESC
             LIMIT 1) AS m
      JOIN tropical_track_points_in_force AS p
        ON p.storm_id = %(storm_id)s AND p.source = 'nhc_official' AND p.init_ts = m.init_ts
     GROUP BY p.advisory, p.init_ts
     ORDER BY position_valid_ts DESC
     LIMIT 1
"""

# One named advisory's synoptic time (init_ts) and own position, off the same index.
ADVISORY_AT_SQL = """
    SELECT advisory, init_ts, min(valid_ts) AS position_valid_ts
      FROM tropical_track_points_in_force
     WHERE storm_id = %(storm_id)s AND source = 'nhc_official' AND advisory = %(advisory)s
     GROUP BY advisory, init_ts
     ORDER BY init_ts DESC
     LIMIT 1
"""

# NHC's own word on what it issued with an advisory: the newest CurrentStorms
# entry the bank holds for it (vintage_key '<ADV>:<sha8>', one per revision of
# the entry). meta.refs names, per product, the advisory whose file NHC linked.
# The ledger collates C (Neon: C.UTF-8), so the prefix LIKE is an index range of
# tfv_identity.
ENTRY_SQL = """
    SELECT vintage_key, issued_ts, fetch_ts, meta
      FROM tropical_file_vintage_in_force
     WHERE source = 'nhc' AND product = 'storm_entry' AND storm_id = %(storm_id)s
       AND vintage_key LIKE %(entry_prefix)s
     ORDER BY fetch_ts DESC, vintage_id DESC
     LIMIT 1
"""

# The copies in force of one (storm_id, vintage_key)'s files, NHC's and the
# display bucket's, each named: tfv_identity probes. A corrected copy carries
# its original (the banked copy with no meta.correction, the first fetched):
# one more probe, only where corrected.
FILES_SQL = """
    SELECT v.source, v.product, v.vintage_key, v.r2_key, v.sha256, v.byte_size,
           v.issued_ts, v.fetch_ts, v.meta, v.revision, v.corrected,
           o.r2_key AS original_r2_key, o.sha256 AS original_sha256,
           o.byte_size AS original_bytes
      FROM tropical_file_vintage_in_force AS v
      LEFT JOIN LATERAL (
            SELECT a.r2_key, a.sha256, a.byte_size
              FROM tropical_file_vintage AS a
             WHERE v.corrected
               AND a.source = v.source AND a.product = v.product
               AND a.storm_id = v.storm_id AND a.vintage_key = v.vintage_key
               AND a.status = 'banked'
               AND NOT (a.meta ? 'correction') AND NOT (a.meta ? 'anomaly_prior_sha')
             ORDER BY a.fetch_ts, a.vintage_id
             LIMIT 1) AS o ON true
     WHERE v.source = ANY (%(sources)s::text[]) AND v.product = ANY (%(products)s::text[])
       AND v.storm_id = %(storm_id)s AND v.vintage_key = %(vintage_key)s
     ORDER BY v.source, v.product
"""

# Every banked member parquet of one storm, and every published manifest, per
# cycle (vintage_key): one tfv_identity range per (source, product, storm_id).
MEMBERS_SQL = """
    SELECT v.source, v.product, v.vintage_key, v.r2_key, v.sha256, v.byte_size,
           v.issued_ts, v.meta, v.revision, v.corrected
      FROM tropical_file_vintage_in_force AS v
     WHERE v.source = ANY (%(sources)s::text[]) AND v.product = ANY (%(products)s::text[])
       AND v.storm_id = %(storm_id)s
       AND (%(cycle)s::text IS NULL OR v.vintage_key = %(cycle)s)
     ORDER BY v.vintage_key, v.product
"""


def files_params(storm_id: str, vintage_key: str, pairs) -> dict:
    """FILES_SQL's parameters for (served name, NHC product) pairs."""
    products = list(dict.fromkeys([p for _, p in pairs] + [display_product(n) for n, _ in pairs]))
    return {"sources": sorted({NHC_SOURCE, DISPLAY_SOURCE}), "products": products,
            "storm_id": storm_id, "vintage_key": vintage_key}


def members_params(storm_id: str, cycle: Optional[str]) -> dict:
    products = [p for _, p in ENSEMBLES] + [members_product(e) for e, _ in ENSEMBLES]
    return {"sources": [DISPLAY_SOURCE], "products": products, "storm_id": storm_id,
            "cycle": cycle}


def entry_params(storm_id: str, advisory: str) -> dict:
    return {"storm_id": storm_id, "entry_prefix": f"{advisory}:%"}


def entry_advisory(entry: dict) -> str:
    """The advisory an entry is of: its vintage_key is '<ADV>:<sha8>'."""
    return entry["vintage_key"].split(":", 1)[0]


# ── small helpers ───────────────────────────────────────────────────────────

_ts = _tr._ts
_absence = _tr._absence


def _by_product(rows: list) -> dict:
    """{(source, product): row}. The view serves one copy in force per identity,
    so two rows here mean the in-force rule did not hold: refuse, never pick."""
    out = {}
    for r in rows:
        k = (r["source"], r["product"])
        if k in out:
            raise RuntimeError(f"two copies in force of {k} at one vintage_key: the in-force rule "
                               f"did not hold")
        out[k] = r
    return out


def _original(r: dict) -> Optional[dict]:
    if not r.get("corrected"):
        return None
    if r.get("original_r2_key") is None:
        return {"url": None, "sha256": None, "bytes": None,
                "absence": _absence("original_not_held",
                                    "the ledger holds no uncorrected copy of this object")}
    return {"url": display_url(r["original_r2_key"]), "sha256": r["original_sha256"],
            "bytes": r["original_bytes"], "absence": None}


def _provenance(r: dict, src: Optional[dict]) -> dict:
    """The published object's own correction state, and whether the NHC file it
    names is the one in force now (a correction NHC issued after publishing)."""
    meta = r["meta"] or {}
    return {"revision": r["revision"], "corrected": r["corrected"], "original": _original(r),
            "source_in_force": None if src is None else meta.get("source_sha256") == src["sha256"]}


def _object(name: str, r: dict, src: Optional[dict]) -> dict:
    meta = r["meta"] or {}
    return {"product": name, "url": display_url(r["r2_key"]), "sha256": r["sha256"],
            "bytes": r["byte_size"], "source_r2_key": meta.get("source_r2_key"),
            "source_sha256": meta.get("source_sha256"),
            "geometry_types": meta.get("geometry_types"), "vertices": meta.get("vertices"),
            **_provenance(r, src)}


def _entry_ref(entry: Optional[dict], nhc_product: str) -> tuple[str, Optional[str]]:
    """What NHC's newest entry for the advisory links for one product:
    ('no_entry', None) | ('none', None) | ('ref', advisory it links)."""
    if entry is None:
        return "no_entry", None
    refs = (entry.get("meta") or {}).get("refs") or {}
    ref = refs.get(nhc_product)
    return ("none", None) if ref is None else ("ref", ref)


def _absent(name: str, nhc_product: str, *, advisory: str, src: Optional[dict],
            entry: Optional[dict], basin: Optional[str] = None) -> dict:
    """Why one product of one advisory is not served: the first that holds of
    basin_has_none, not_published, not_issued, not_banked."""
    if name == "surge_ww" and basin not in SURGE_BASINS:
        return {"product": name, "reason": "basin_has_none",
                "detail": f"NHC issues storm surge watches and warnings for the US Atlantic and "
                          f"Gulf coasts only; this storm's basin is '{basin}'"}
    if src is not None:
        return {"product": name, "reason": "not_published",
                "detail": f"NHC's {nhc_product} for advisory {advisory} is banked "
                          f"({src['r2_key']}, sha256 {src['sha256']}); the ledger holds no "
                          f"published {display_product(name)} object for it"}
    kind, ref = _entry_ref(entry, nhc_product)
    if kind == "none":
        return {"product": name, "reason": "not_issued",
                "detail": f"NHC's CurrentStorms entry for advisory {advisory} (the newest the bank "
                          f"holds, {entry['vintage_key']}, fetched {_ts(entry['fetch_ts'])}) "
                          f"links no {nhc_product}"}
    if kind == "ref" and ref != advisory:
        return {"product": name, "reason": "not_issued",
                "detail": f"NHC's CurrentStorms entry for advisory {advisory} (the newest the bank "
                          f"holds, {entry['vintage_key']}, fetched {_ts(entry['fetch_ts'])}) links "
                          f"advisory {ref}'s {nhc_product}: NHC issued none with {advisory}"}
    if kind == "ref":
        return {"product": name, "reason": "not_banked",
                "detail": f"NHC's CurrentStorms entry links advisory {advisory}'s {nhc_product}; "
                          f"the bank holds no copy of it"}
    return {"product": name, "reason": "not_banked",
            "detail": f"the bank holds no {nhc_product} for advisory {advisory}, and no "
                      f"CurrentStorms entry for the advisory to say whether NHC issued one"}


def _entry_obj(entry: Optional[dict]) -> Optional[dict]:
    if entry is None:
        return None
    meta = entry.get("meta") or {}
    return {"vintage_key": entry["vintage_key"], "issued_ts": _ts(entry["issued_ts"]),
            "fetch_ts": _ts(entry["fetch_ts"]), "classification": meta.get("classification"),
            "refs": meta.get("refs") or {}}


def wsp_cycle(init_ts) -> Optional[str]:
    """The WSP cycle of an advisory: its synoptic time (the official forecast's
    init_ts) as YYYYMMDDHH. Recon §3: Rachel 058 (15Z) -> 2026101112, Simon 032
    (09Z) -> 2026101506."""
    if init_ts is None:
        return None
    if isinstance(init_ts, str):
        init_ts = datetime.fromisoformat(init_ts)
    return init_ts.astimezone(timezone.utc).strftime("%Y%m%d%H")


def _served_and_absent(pairs, rows, *, advisory, entry, basin) -> tuple[list, list]:
    by = _by_product(rows)
    objects, absent = [], []
    for name, nhc_product in pairs:
        src = by.get((NHC_SOURCE, nhc_product))
        pub = by.get((DISPLAY_SOURCE, display_product(name)))
        if pub is not None:
            objects.append(_object(name, pub, src))
        else:
            absent.append(_absent(name, nhc_product, advisory=advisory, src=src, entry=entry,
                                  basin=basin))
    return objects, absent


def _advisory_head(storm: dict, advisory: Optional[str], adv: Optional[dict],
                   requested: Optional[str], entry: Optional[dict]) -> dict:
    cur = _tr.current_name(storm["names"])
    return {
        "storm_id": storm["storm_id"], "name": cur["name"] if cur else None,
        "basin": storm["basin"],
        "advisory": advisory,
        "advisory_requested": requested is not None,
        "init_ts": _ts(adv["init_ts"]) if adv else None,
        "position_valid_ts": _ts(adv["position_valid_ts"]) if adv else None,
        "issued_ts": _ts(entry["issued_ts"]) if entry else None,
        "nhc_entry": _entry_obj(entry),
        "nhc_entry_absence": None if entry is not None else _absence(
            "no_entry", "the bank holds no CurrentStorms entry for this advisory"),
    }


ADVISORY_DEFAULT_RULE = ("Without `advisory`, NHC's newest advisory for the storm: the one its "
                         "newest CurrentStorms entry is of (issued_ts, then fetch), so a "
                         "text-only post-tropical advisory counts; for a storm with no entry, "
                         "the newest official forecast (newest init_ts, then position valid "
                         "time, never the string). Per product, the copy in force (pantry 294). "
                         "init_ts is the advisory's synoptic time, from its official forecast; "
                         "null where NHC issued none.")


def no_advisory(storm: dict) -> dict:
    """The storm is known and the bank holds no official advisory for it."""
    return {**_advisory_head(storm, None, None, None, None),
            "absence": _absence("no_advisory",
                                "the bank holds no NHC official advisory for this storm")}


# ── /products ───────────────────────────────────────────────────────────────

def build_products(*, storm: dict, advisory: Optional[str], adv: Optional[dict],
                   requested: Optional[str], entry: Optional[dict], rows: list,
                   wsp_rows: list) -> dict:
    """GET /products. One advisory's published geometry, and its cycle's WSP.
    `advisory` is the one resolved (requested or default); `adv` its official
    forecast's row (init_ts: the synoptic time), None where NHC issued none."""
    if advisory is None:
        body = {**no_advisory(storm), "revision": 0, "corrected": False, "objects": [],
                "absent": [], "wsp": None}
    else:
        objects, absent = _served_and_absent(ADVISORY_PRODUCTS, rows, advisory=advisory,
                                             entry=entry, basin=storm["basin"])
        cycle = wsp_cycle(adv["init_ts"]) if adv else None
        if cycle is None:
            wsp = {"cycle": None, "synoptic_ts": None, "objects": [], "absent": [],
                   "absence": _absence("no_synoptic_time",
                                       f"the bank holds no official forecast for advisory "
                                       f"{advisory} (NHC issues none with a text-only "
                                       f"advisory), so its synoptic time, the WSP cycle, is "
                                       f"not known")}
        else:
            wobj, wabs = [], []
            by = _by_product(wsp_rows)
            for name, nhc_product in WSP_PRODUCTS:
                src = by.get((NHC_SOURCE, nhc_product))
                pub = by.get((DISPLAY_SOURCE, display_product(name)))
                if pub is not None:
                    wobj.append(_object(name, pub, src))
                elif src is not None:
                    wabs.append({"product": name, "reason": "not_published",
                                 "detail": f"NHC's {nhc_product} for cycle {cycle} is banked "
                                           f"({src['r2_key']}, sha256 {src['sha256']}); the ledger "
                                           f"holds no published {display_product(name)} object"})
                else:
                    wabs.append({"product": name, "reason": "not_banked",
                                 "detail": f"the bank holds no {nhc_product} for cycle {cycle}"})
            wsp = {"cycle": cycle, "synoptic_ts": _ts(adv["init_ts"]), "objects": wobj,
                   "absent": wabs, "absence": None}
        body = {**_advisory_head(storm, advisory, adv, requested, entry),
                "revision": max((o["revision"] for o in objects), default=0),
                "corrected": any(o["corrected"] for o in objects),
                "objects": objects, "absent": absent, "wsp": wsp, "absence": None}
    body.update({
        "advisory_rule": ADVISORY_DEFAULT_RULE,
        "wsp_rule": ("WSP is issued per synoptic cycle, basin-wide, not per storm: the cycle is "
                     "the advisory's synoptic time (its official forecast's init_ts) as "
                     "YYYYMMDDHH. Bands are the 5 km zip's contours (11 percentage bands); the "
                     "grid is the half-degree zip's points, value in percent, cells below 0.1 % "
                     "absent."),
        "revision_rule": ("revision and corrected are each object's own (the ledger's copy in "
                          "force and meta.correction); `original` is the uncorrected copy where "
                          "one is corrected. The top-level revision is the highest served; "
                          "source_in_force says whether the NHC file an object names is the "
                          "copy in force now."),
        "index_rule": INDEX_RULE, "reasons": REASONS, "timestamps": _tr.TIMESTAMPS,
        "not_served": NOT_SERVED_INDEX, "nhc_terms": _tr.NHC_TERMS,
    })
    return body


# ── /text ───────────────────────────────────────────────────────────────────

def _text_obj(name: str, r: dict, src: Optional[dict]) -> dict:
    meta = r["meta"] or {}
    return {"product": name, "url": display_url(r["r2_key"]), "sha256": r["sha256"],
            "bytes": r["byte_size"], "issued_utc": meta.get("issued_utc"),
            "transmitted_utc": meta.get("transmitted_utc"), "forecaster": meta.get("forecaster"),
            "sections": [{"title": s.get("title"), "kind": s.get("kind")}
                         for s in (meta.get("sections") or [])],
            "source_r2_key": meta.get("source_r2_key"), "source_sha256": meta.get("source_sha256"),
            **_provenance(r, src), "absence": None}


def build_text(*, storm: dict, advisory: Optional[str], adv: Optional[dict],
               requested: Optional[str], entry: Optional[dict], rows: list) -> dict:
    """GET /text. One advisory's TCD and TCP as published, sections listed."""
    if advisory is None:
        body = {**no_advisory(storm), "products": {}}
    else:
        by = _by_product(rows)
        products = {}
        for name, nhc_product in TEXT_PRODUCTS:
            src = by.get((NHC_SOURCE, nhc_product))
            pub = by.get((DISPLAY_SOURCE, display_product(name)))
            if pub is not None:
                products[name] = _text_obj(name, pub, src)
            else:
                a = _absent(name, nhc_product, advisory=advisory, src=src, entry=entry)
                products[name] = {"product": name, "url": None, "sha256": None, "bytes": None,
                                  "issued_utc": None, "transmitted_utc": None, "forecaster": None,
                                  "sections": [], "source_r2_key": None, "source_sha256": None,
                                  "revision": None, "corrected": None, "original": None,
                                  "source_in_force": None,
                                  "absence": {"reason": a["reason"], "detail": a["detail"]}}
        body = {**_advisory_head(storm, advisory, adv, requested, entry), "products": products,
                "absence": None}
    body.update({
        "advisory_rule": ADVISORY_DEFAULT_RULE,
        "times_rule": ("Two times, and they differ: transmitted_utc is the WMO heading's DDHHMM "
                       "(when NHC transmitted the product), issued_utc the product's local "
                       "issuance line in UTC (the advisory's nominal time). The TCD's forecast "
                       "table counts from the advisory time, the bank's official track from the "
                       "synoptic time: never join them on tau."),
        "sections_rule": ("sections are the parse's own, in NHC's order, title as printed and kind "
                          "as the parser names it; the text is in the object at url."),
        "index_rule": INDEX_RULE, "reasons": REASONS, "timestamps": _tr.TIMESTAMPS,
        "not_served": NOT_SERVED_INDEX, "nhc_terms": _tr.NHC_TERMS,
    })
    return body


# ── /members ────────────────────────────────────────────────────────────────

MEMBERS_RULE = ("A published ensemble is a display-bucket manifest (schema tropics.members.v1, "
                "recon d091737 §4) in the ledger as product 'display:members:<ensemble>', "
                "vintage_key the cycle. n_members, min_members and taus are the manifest's own "
                "(its ledger meta); an ensemble whose manifest states fewer members than its own "
                "minimum is listed absent as too_few_members, with its url. A banked member "
                "parquet with no manifest is not_published; a cycle with no parquet is "
                "not_banked.")


def build_members(*, storm: dict, cycle: Optional[str], rows: list) -> dict:
    """GET /members. Per cycle, each ensemble's published manifest or its absence."""
    cur = _tr.current_name(storm["names"])
    banked, published = {}, {}
    banked_of = {p: e for e, p in ENSEMBLES}
    published_of = {members_product(e): e for e, _ in ENSEMBLES}
    for r in rows:
        if r["product"] in banked_of:
            banked[(r["vintage_key"], banked_of[r["product"]])] = r
        elif r["product"] in published_of:
            published[(r["vintage_key"], published_of[r["product"]])] = r
    cycles_held = sorted({k[0] for k in banked} | {k[0] for k in published})
    cycles = [cycle] if cycle is not None else cycles_held
    served, absent = [], []
    for c in cycles:
        ens = []
        for e, product in ENSEMBLES:
            pub, bank = published.get((c, e)), banked.get((c, e))
            if pub is not None:
                meta = pub["meta"] or {}
                n, floor = meta.get("n_members"), meta.get("min_members")
                if n is not None and floor is not None and n < floor:
                    absent.append({"cycle": c, "ensemble": e, "reason": "too_few_members",
                                   "detail": f"the published manifest states {n} members against "
                                             f"its own minimum of {floor}",
                                   "manifest_url": display_url(pub["r2_key"])})
                    continue
                year = int(c[:4])
                ens.append({"ensemble": e, "label": ENSEMBLE_LABELS[e],
                            "manifest_url": display_url(pub["r2_key"]), "sha256": pub["sha256"],
                            "bytes": pub["byte_size"], "n_members": n, "min_members": floor,
                            "taus": meta.get("taus"), "source_r2_key": meta.get("source_r2_key"),
                            "source_sha256": meta.get("source_sha256"),
                            "revision": pub["revision"], "corrected": pub["corrected"],
                            "notice": _tr.ECMWF_NOTICE.format(year=year)
                            if e in ENSEMBLE_ECMWF else None})
            elif bank is not None:
                meta = bank["meta"] or {}
                held = (f"{meta['n_members']} members by the bank's meta"
                        if e != "gefs_adeck" and meta.get("n_members") is not None else
                        "the whole A-deck; GEFS members are told by source name (AP01-AP30), "
                        "the bank's n_members counts a member column the A-deck parser leaves "
                        "null")
                absent.append({"cycle": c, "ensemble": e, "reason": "not_published",
                               "detail": f"banked as R2 parquet {bank['r2_key']} (sha256 "
                                         f"{bank['sha256']}, {held}); no published manifest is "
                                         f"in the ledger (the members publish step is d091743's)"})
            else:
                absent.append({"cycle": c, "ensemble": e, "reason": "not_banked",
                               "detail": f"the bank holds no {product} parquet for this storm at "
                                         f"cycle {c}"})
        if ens:
            served.append({"cycle": c, "ensembles": ens})
    absence = None
    if not cycles:
        absence = _absence("no_cycle", "the bank holds no member parquet and no published "
                                       "manifest for this storm")
    elif not served:
        absence = _absence("none_published",
                           f"no ensemble manifest is published for "
                           f"{'cycle ' + cycle if cycle else 'any cycle the bank holds'}: every "
                           f"cycle and ensemble is listed in `absent` with its reason")
    return {
        "storm_id": storm["storm_id"], "name": cur["name"] if cur else None,
        "cycle": cycle, "cycles_held": cycles_held,
        "cycles": served, "absent": absent, "absence": absence,
        "ensembles": [e for e, _ in ENSEMBLES],
        "order": "oldest cycle first",
        "members_rule": MEMBERS_RULE, "index_rule": INDEX_RULE,
        "reasons": {"not_banked": REASONS["not_banked"] + " (the member parquet)",
                    "not_published": "the member parquet is banked; no manifest is published",
                    "too_few_members": "the published manifest states fewer members than its "
                                       "own minimum"},
        "timestamps": _tr.TIMESTAMPS,
        "not_served": {**NOT_SERVED_INDEX,
                       "member_points": ("No member point is served or stored in Neon: the "
                                         "points are in the manifest's members.geojson in the "
                                         "display bucket.")},
    }


# ── /ground: the GIBS frame probe ───────────────────────────────────────────
#
# Recon d091737 §1: GIBS's `default` time served frames 20-60 minutes older
# than the newest present, differently per layer and minute (CloudFront caches
# it); DescribeDomains and the capabilities' <Default> run behind AND ahead of
# the tile store; a slot inside a listed interval can be missing. So a listed
# time is a claim and a 200 on the tile is the receipt. Measured here
# (2026-10-15): a 200 carries GIBS's `layer-time-actual`, the time it served,
# and frames land out of order (East 13:30 present while 13:20 was 404).

GIBS_BASE = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
GIBS_TMS = "GoogleMapsCompatible_Level7"
GIBS_LAYERS = {
    "GOES-East_ABI_GeoColor": {"satellite": "GOES-East", "instrument": "ABI",
                               "title": "GeoColor (True Color (Day), Multispectral IR (Night))"},
    "GOES-West_ABI_GeoColor": {"satellite": "GOES-West", "instrument": "ABI",
                               "title": "GeoColor (True Color (Day), Multispectral IR (Night))"},
}
GROUND_LAYERS_BY_BASIN = {"al": "GOES-East_ABI_GeoColor", "ep": "GOES-West_ABI_GeoColor",
                          "cp": "GOES-West_ABI_GeoColor"}
SLOT_MIN = 10
WINDOW_SLOTS = 18           # 3 h: give up after this many slots without a frame
RECENT_SLOTS = 12           # recent_present: the frame and the 11 slots below it
TILE_SIZE = 256
MINZOOM, MAXZOOM = 0, 6     # the layer's TileMatrixSetLimits stop at z6 (recon §1)

# GIBS's published acknowledgement, read by d091741 from nasa-gibs/gibs-api-docs
# (github.com, the source of nasa-gibs.github.io/gibs-api-docs/) at commit
# afb15814aee74b09e672466adb731b2e34d51d8b, docs/index.md (sha256 c3cc2ba2…), section
# "Data Use Guidance and Acknowledgements". Pinned: tests/fixtures/tropics_d091741/
# attribution/. The capabilities documents carry no attribution text (read the same
# day: <Fees>none</Fees>, <AccessConstraints>none</AccessConstraints>, no <Attribution>).
GIBS_ACKNOWLEDGEMENT = ("We acknowledge the use of imagery provided by services from NASA's "
                        "Global Imagery Browse Services (GIBS), part of NASA's Earth Science Data "
                        "and Information System (ESDIS).")
GIBS_ATTRIBUTION = {
    "text": GIBS_ACKNOWLEDGEMENT,
    "source": ("nasa-gibs/gibs-api-docs docs/index.md, 'Data Use Guidance and Acknowledgements' "
               "(published at https://nasa-gibs.github.io/gibs-api-docs/), commit "
               "afb15814aee74b09e672466adb731b2e34d51d8b, read 2026-10-15"),
    "layer_credit": ("Layer titles are GIBS's own (layer-metadata/v1.0). GIBS publishes no "
                     "credit line for GeoColor's producer; none is added here."),
}

# fetch(url) -> (status, content_type, layer_time_actual); raises on transport failure.
Fetch = Callable[[str], Awaitable[tuple]]


def _slot(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def tile_url(layer: str, time: str, z: int = 0, y: int = 0, x: int = 0) -> str:
    return f"{GIBS_BASE}/{layer}/default/{time}/{GIBS_TMS}/{z}/{y}/{x}.png"


def classify(slot: str, result: tuple) -> str:
    """'present' | 'absent' | 'error' for one tile answer. Present is a 200 PNG
    that GIBS says is this slot's (layer-time-actual, where it sends one)."""
    status, ctype, actual = result
    if status == 200:
        if not (ctype or "").lower().startswith("image/"):
            return "absent"
        if actual is not None and actual != slot:
            return "absent"                     # GIBS served another time for this slot
        return "present"
    if status == 404:
        return "absent"
    return "error"


async def probe(layer: str, now: datetime, fetch: Fetch, *, budget_s: Optional[float] = None,
                clock: Callable[[], float] = time.monotonic) -> dict:
    """The newest PRESENT frame of one layer: z0 at descending 10-minute slots
    from now, first present wins, give up after WINDOW_SLOTS. Then the frame's
    RECENT_SLOTS-1 older slots, asked together, for recent_present. A search
    that runs past budget_s stops there as gibs_unreachable."""
    now = now.astimezone(timezone.utc)
    slot0 = now.replace(minute=now.minute - now.minute % SLOT_MIN, second=0, microsecond=0)
    asked = []
    frame = None
    t0 = clock()
    for k in range(WINDOW_SLOTS):
        t = slot0 - timedelta(minutes=SLOT_MIN * k)
        s = _slot(t)
        if budget_s is not None and clock() - t0 > budget_s:
            return {"layer": layer, "probed_utc": now, "frame": None, "asked": asked,
                    "recent_present": [], "recent_unknown": [],
                    "refusal": ("gibs_unreachable",
                                f"the probe ran past its {budget_s:g} s budget after {len(asked)} "
                                f"slot(s), none present")}
        try:
            res = await fetch(tile_url(layer, s))
        except Exception as e:                           # transport: GIBS not reached
            asked.append({"slot_utc": s, "verdict": "error", "status": None})
            return {"layer": layer, "probed_utc": now, "frame": None, "asked": asked,
                    "recent_present": [], "recent_unknown": [],
                    "refusal": ("gibs_unreachable",
                                f"the z0 tile for {s} could not be fetched "
                                f"({type(e).__name__}: {e}); {len(asked) - 1} slot(s) answered "
                                f"before it, none present")}
        v = classify(s, res)
        asked.append({"slot_utc": s, "verdict": v, "status": res[0]})
        if v == "error":
            return {"layer": layer, "probed_utc": now, "frame": None, "asked": asked,
                    "recent_present": [], "recent_unknown": [],
                    "refusal": ("gibs_unreachable",
                                f"GIBS answered {res[0]} for the z0 tile at {s}; "
                                f"{len(asked) - 1} slot(s) answered before it, none present")}
        if v == "present":
            frame = t
            break
    if frame is None:
        return {"layer": layer, "probed_utc": now, "frame": None, "asked": asked,
                "recent_present": [], "recent_unknown": [],
                "refusal": ("no_frame_in_window",
                            f"{WINDOW_SLOTS} ten-minute slots probed "
                            f"({asked[-1]['slot_utc']} to {asked[0]['slot_utc']}), none present")}
    older = [frame - timedelta(minutes=SLOT_MIN * i) for i in range(1, RECENT_SLOTS)]
    answers = await asyncio.gather(*(fetch(tile_url(layer, _slot(t))) for t in older),
                                   return_exceptions=True)
    present, unknown = [_slot(frame)], []
    for t, a in zip(older, answers):
        s = _slot(t)
        v = "error" if isinstance(a, BaseException) else classify(s, a)
        if v == "present":
            present.append(s)
        elif v == "error":
            unknown.append(s)
    return {"layer": layer, "probed_utc": now, "frame": frame, "asked": asked,
            "recent_present": present, "recent_unknown": unknown, "refusal": None}


def probe_log(p: dict) -> dict:
    """One line per probe (the 'three times of day' measurement recon §1 could
    not make): when, which layer, the frame found and its age, what was missed."""
    age = None if p["frame"] is None else round((p["probed_utc"] - p["frame"]).total_seconds() / 60, 1)
    return {"probe": "gibs_geocolor_z0", "layer": p["layer"],
            "probed_utc": _ts(p["probed_utc"]),
            "frame_utc": _ts(p["frame"]) if p["frame"] else None, "age_min": age,
            "slots_asked": len(p["asked"]),
            "absent_before_frame": [a["slot_utc"] for a in p["asked"] if a["verdict"] == "absent"],
            "recent_present": len(p["recent_present"]),
            "refusal": p["refusal"][0] if p["refusal"] else None}


def build_ground(p: dict) -> dict:
    """GET /ground (per layer; the route adds `basin`)."""
    layer = p["layer"]
    meta = GIBS_LAYERS[layer]
    if p["refusal"] is None:
        frame = _slot(p["frame"])
        layers = [{
            "id": layer, "satellite": meta["satellite"], "instrument": meta["instrument"],
            "title": meta["title"],
            "template": tile_url(layer, frame, "{z}", "{y}", "{x}"),
            "time_template": tile_url(layer, "{Time}", "{z}", "{y}", "{x}"),
            "tile_matrix_set": GIBS_TMS, "tile_size": TILE_SIZE,
            "minzoom": MINZOOM, "maxzoom": MAXZOOM,
            "frame_utc": _ts(p["frame"]), "probed_utc": _ts(p["probed_utc"]),
            "age_min": round((p["probed_utc"] - p["frame"]).total_seconds() / 60, 1),
            "recent_present": p["recent_present"], "recent_unknown": p["recent_unknown"],
        }]
        absence = None
    else:
        layers = []
        absence = _absence(*p["refusal"])
    return {
        "layers": layers, "attribution": GIBS_ATTRIBUTION, "absence": absence,
        "probe": probe_log(p),
        "frame_rule": ("frame_utc is the newest 10-minute slot whose z0 tile GIBS served as a PNG "
                       "of that time (a 200, and layer-time-actual equal to the slot where GIBS "
                       "sends it), probed down from now; GIBS's `default` time, its "
                       "DescribeDomains and its capabilities <Default> are never used (recon "
                       "d091737 §1: each lagged or led the tiles). age_min is at probed_utc."),
        "recent_rule": (f"recent_present lists the frame and every present slot among the "
                        f"{RECENT_SLOTS - 1} slots below it, newest first; recent_unknown the "
                        f"slots whose answer was an error. A slot in neither was absent."),
        "basin_rule": "East for al, West for ep and cp",
        "timestamps": _tr.TIMESTAMPS,
        "not_served": {"default_time": ("GIBS's `default` time is never bound: it served frames "
                                        "20-60 minutes older than the newest present, by layer "
                                        "and minute (recon d091737 §1).")},
    }
