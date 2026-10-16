"""The Tropics index — d091741 (D-09-25-226, orders 1-4: the API half), reading no R2;
the display read by revision — d091746 (D-09-25-231); the 7-day outlook and the
arrival-time objects — d091757 (D-09-25-234, D-09-25-236).

    GET /api/weather/tropics/ground?basin=al|ep|cp
                                        the GOES GeoColor frame GIBS holds now, probed
                                        tile by tile (never GIBS's `default`)
    GET /api/weather/tropics/products?storm_id=&advisory=
                                        the published cone / watch-warning / surge
                                        objects of one advisory, its arrival-time (TOA)
                                        objects with their label tables, and the WSP of
                                        its synoptic cycle, each stated or absent with a reason
    GET /api/weather/tropics/outlook?basin=al|ep|cp&stamp=
                                        NHC's Tropical Weather Outlook: the graphical
                                        issuance (areas, points) at its 12-digit stamp,
                                        newest by default, and the basin's outlook text
                                        in force at that stamp, each by its own stamp
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

THE LEDGER CONTRACT, AND WHERE IT CAME FROM. d091741 was written before d091740 (the
pantry's publish step) landed, against recon d091737 §7's proposal; the publisher
(tropical/publish.py, migrations 314 and 316) confirmed it, with the corrections keyed
as below (THE REVISION READ). The rows, as recon d091737 §7 proposes them (pantry
docs/recon_2026_10_15_tropics_v2_layers_d091737.md): one vintage-ledger row per published object, `product` = 'display:<name>',
`r2_key` = the display key, sha256 and byte_size of the published bytes, `meta` = the
K1 summary plus the source object's key and sha. The two names the recon left open
are pinned here, in ONE place each:

  * DISPLAY_SOURCE = 'display'. d091740 confirmed the recon's name and migration 314
    (applied 2026-10-15 15:03Z) re-cut the ledger's source CHECK to admit it, with
    tfv_display_is_display_product: (source = 'display') = (product LIKE 'display:%').
    This file first pinned 'parsed' (291's six sources held no 'display' and 'parsed'
    is the bank's source for the 'tracks:*' parquets); the first backfill (run
    38758258112, 454 rows) landed under 'display' and every index read answered
    not_published over a full ledger — D-09-05-T, the cross-repo pin that drifted.
    PARSED_SOURCE stays the member parquets' source; /members reads both.
  * the meta keys each route reads (META_* below). /text reads the publisher's
    section_kinds, title and n_forecast_rows (d091746: d091741 read a `sections` key no
    writer produces, and every live /text served sections: []).

THE REVISION READ (d091746). The publisher (pantry tropical/publish.py, d091740) keys a
correction NHC issued as its OWN row, vintage_key '<ADV>r<n>' (or '<cycle>r<n>'), one
row per (product, storm_id, vintage_key) (pantry 314: tfv_display_immutable,
tfv_display_vintage_key; 316 admits a 12-digit outlook stamp as a base). So a display
row is read through 314's own reader, tropical_display_in_force: per (product,
storm_id, advisory_or_cycle) the highest revision, then the newest publish
(D-09-25-226 §2/§5: readers use the copy in force). `revision` is the view's (the
key's r<n>), `corrected` is revision > 0, and a corrected object's `original` is
the revision-0 row at the same identity (vintage_key = advisory_or_cycle). NHC's
own files are still read through 294's tropical_file_vintage_in_force by exact
vintage_key, and the member parquets likewise (source 'parsed'); no display row
is read through 294's view. Before d091746 this module asked 294's view for
vintage_key = '<ADV>', so a corrected advisory was served its revision-0 object.

THE OUTLOOK'S KEY (d091757; D-09-25-234, pantry migration 316). A TWO issuance is
keyed by its 12-digit stamp, YYYYMMDDHHMM: vintage_key = the stamp, and the view's
advisory_or_cycle is the stamp (316 widened the base pattern to read it whole). r<n> on
a TWO row means a correction NHC issued over the same stamp, never another issuance.
Two outlooks of one hour are two issuances (10-14 05:17Z and 05:22Z; 10-15 23:11Z and
23:47Z are banked): no read here truncates a stamp to its hour, and `stamp` refuses an
hour and refuses r<n>. The graphics (two_areas, two_points: one gtwo zip) share the
zip's stamp; each text has its own (its WMO DDHHMM, e.g. 10-15 two_text_ep 23:10Z, the
zip re-cut at 23:11Z), so a text is paired to the graphics by stamp: the basin's text
with the newest stamp at or before the graphics' (the text in force when the zip was
cut). Each object prints its own stamp. The newest stamp of a product is one
tfv_identity range read backwards, LIMIT 1 (CLAUDE.md d091551: not the view's DISTINCT
ON, which reads every issuance to keep one); the object itself is read through 314's
view at that exact stamp, as every display object is.

THE ARRIVAL-TIME OBJECTS (d091757; D-09-25-236). toa_earliest and toa_mostlikely are
served on /products as display objects of the advisory, read exactly as the cone is
(the copy in force on 314's view, revision / corrected / original), plus the label table
their row carries, under the row's own names and never a key the publisher does not write
(D-09-25-242 §1(d)): labels (the publisher's object verbatim: n, null, parsed, backwards,
by_source, icon_bands, not_a_time), labels_null, timezone and withdrawal, each served
where the row carries it. No label is parsed, inferred or filled here: whatever row is in
force is what is served (since D-09-25-236's withdrawal, the corrected parser's).

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


# An outlook issuance as the publisher keys it (D-09-25-234): YYYYMMDDHHMM, nothing after it.
STAMP = re.compile(r"^20[0-9]{2}(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])([01][0-9]|2[0-3])[0-5][0-9]$")
# Every banked stamp sorts at or below this one.
STAMP_MAX = "999999999999"


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


def parse_stamp(raw: Optional[str]) -> Optional[str]:
    """An outlook's issuance stamp, YYYYMMDDHHMM (D-09-25-234). An hour (YYYYMMDDHH) is not
    a stamp: two outlooks of one hour are two issuances. r<n> is not an issuance: it is a
    correction NHC issued over a stamp, and the stamp's copy in force is served."""
    if raw is None or raw == "":
        return None
    if re.match(r"^[0-9]{12}r[0-9]+$", raw):
        raise ValueError(f"stamp {raw!r} names a correction (r<n>), not an issuance: ask for the "
                         f"stamp {raw[:12]}; its copy in force is served, revision stated")
    if re.match(r"^[0-9]{10}$", raw):
        raise ValueError(f"stamp {raw!r} is an hour (YYYYMMDDHH), not an issuance: an outlook is "
                         f"keyed by its 12-digit stamp YYYYMMDDHHMM, and two of one hour are two")
    if not STAMP.match(raw):
        raise ValueError(f"stamp must be the outlook's 12-digit issuance stamp YYYYMMDDHHMM "
                         f"(e.g. 202610152347), got {raw!r}")
    datetime.strptime(raw, "%Y%m%d%H%M")        # 202602311200 is refused, not rolled
    return raw


def parse_basin(raw: Optional[str]) -> str:
    if raw is None or raw == "":
        raise ValueError(f"basin is required: one of {sorted(GROUND_LAYERS_BY_BASIN)}")
    if raw not in GROUND_LAYERS_BY_BASIN:
        raise ValueError(f"basin must be one of {sorted(GROUND_LAYERS_BY_BASIN)}, got {raw!r}")
    return raw


# ── the ledger contract (recon d091737 §7; confirmed by d091740 / migration 314) ──────────────

DISPLAY_BASE = "https://display.energylake.io/"
DISPLAY_SOURCE = "display"   # migration 314; the published objects
PARSED_SOURCE = "parsed"     # the bank's own derivations: the tracks:* member parquets
NHC_SOURCE = "nhc"
DISPLAY_PREFIX = "display:"

# (served name, the banked NHC file it is published from). Keys, recon §7:
#   tropics/{storm}/adv{ADV}/{cone|ww|surge_ww}.geojson
ADVISORY_PRODUCTS = (("cone", "cone_kmz"), ("ww", "ww_kmz"), ("surge_ww", "surge_ww_kml"))
#   tropics/{storm}/adv{ADV}/toa_{earliest|mostlikely}.geojson (d091756; labels: D-09-25-236)
TOA_PRODUCTS = (("toa_earliest", "toa_earliest_kmz"), ("toa_mostlikely", "toa_mostlikely_kmz"))
#   tropics/{storm}/adv{ADV}/{tcd|tcp}.json (the products_text parse)
TEXT_PRODUCTS = (("tcd", "tcd"), ("tcp", "tcp"))
#   tropics/wsp/{cycle}/wsp{34|50|64}_{bands|grid}.geojson, basin-wide (storm_id '')
WSP_PRODUCTS = (("wsp34_bands", "wsp_5km_zip"), ("wsp50_bands", "wsp_5km_zip"),
                ("wsp64_bands", "wsp_5km_zip"), ("wsp34_grid", "wsp_halfdeg_zip"),
                ("wsp50_grid", "wsp_halfdeg_zip"), ("wsp64_grid", "wsp_halfdeg_zip"))
#   tropics/two/{STAMP}/{areas|points}.geojson and text_{at|ep}.json, basin-wide (storm_id ''),
#   keyed by the 12-digit stamp (d091756, migration 316)
OUTLOOK_GRAPHICS = ("two_areas", "two_points")
# The outlook text each basin is served. NHC's East Pacific outlook is headed "For the
# eastern and central North Pacific east of 180 longitude"; the bank holds no CPHC text.
OUTLOOK_TEXT_BY_BASIN = {"al": "two_text_at", "ep": "two_text_ep", "cp": "two_text_ep"}
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
# The publisher's text meta (tropical/publish.py: the products_text header, plus
# section_kinds and n_forecast_rows); the section titles are in the object only.
META_TEXT = ("source_r2_key", "source_sha256", "issued_utc", "transmitted_utc", "forecaster",
             "title", "n_forecast_rows", "section_kinds")
META_MEMBERS = ("source_r2_key", "source_sha256", "n_members", "min_members", "taus")
# The publisher's TWO meta (tropical/publish.py _two_objects), served verbatim.
META_TWO_GRAPHICS = ("stamp", "issued_utc", "source_layer", "n_features", "n_records", "dbf_fields",
                     "geometry_types", "vertices", "source_r2_key", "source_sha256")
META_TWO_TEXT = ("stamp", "wmo_heading", "awips_id", "bbb", "title", "forecaster", "issued_local",
                 "issued_utc", "transmitted_utc", "formation_not_expected", "n_paragraphs",
                 "n_disturbances", "disturbances", "source_r2_key", "source_sha256")


def display_product(name: str) -> str:
    return f"{DISPLAY_PREFIX}{name}"


def members_product(ensemble: str) -> str:
    return f"{DISPLAY_PREFIX}members:{ensemble}"


def display_url(r2_key: Optional[str]) -> Optional[str]:
    return None if r2_key is None else DISPLAY_BASE + r2_key


INDEX_RULE = ("Each object is a vintage-ledger row of the display bucket, the copy in "
              "force per (product, storm_id, advisory or cycle) as pantry 314's "
              "tropical_display_in_force names it (source '" + DISPLAY_SOURCE + "', product "
              "'display:<name>', r2_key = the display key), served as the ledger states it: "
              "url = " + DISPLAY_BASE + "<r2_key>. The API reads no R2 and fetches no "
              "object. The contract is the publisher's (pantry tropical/publish.py, "
              "migrations 314 and 316).")
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

# NHC's own files of one (storm_id, vintage_key), the copy in force of each
# (pantry 294), by exact key: tfv_identity probes. Only what the absences and
# source_in_force need (the banked key and sha); no display row is read here.
FILES_SQL = """
    SELECT v.source, v.product, v.vintage_key, v.r2_key, v.sha256, v.byte_size,
           v.issued_ts, v.fetch_ts, v.meta, v.revision, v.corrected
      FROM tropical_file_vintage_in_force AS v
     WHERE v.source = 'nhc' AND v.product = ANY (%(products)s::text[])
       AND v.storm_id = %(storm_id)s AND v.vintage_key = %(vintage_key)s
     ORDER BY v.product
"""

# The published objects of one (storm_id, advisory or WSP cycle), the copy in
# force of each (pantry 314's tropical_display_in_force: the highest revision,
# then the newest publish). A corrected object (revision > 0) carries its
# original, the revision-0 row at the same identity, whose vintage_key is the
# bare advisory or cycle (one row: tfv_display_immutable): one more probe, only
# where corrected. WSP is basin-wide: storm_id ''.
DISPLAY_FILES_SQL = """
    SELECT 'display' AS source, v.product, v.advisory_or_cycle, v.vintage_key, v.revision,
           v.revision > 0 AS corrected, v.r2_key, v.sha256, v.byte_size, v.issued_ts,
           v.published_ts, v.meta,
           o.r2_key AS original_r2_key, o.sha256 AS original_sha256,
           o.byte_size AS original_bytes
      FROM tropical_display_in_force AS v
      LEFT JOIN LATERAL (
            SELECT a.r2_key, a.sha256, a.byte_size
              FROM tropical_file_vintage AS a
             WHERE v.revision > 0
               AND a.source = 'display' AND a.product = v.product
               AND a.storm_id = v.storm_id AND a.vintage_key = v.advisory_or_cycle
               AND a.status = 'banked') AS o ON true
     WHERE v.product = ANY (%(products)s::text[]) AND v.storm_id = %(storm_id)s
       AND v.advisory_or_cycle = %(advisory_or_cycle)s
     ORDER BY v.product
"""

# The newest outlook stamp of one product at or before a stamp (d091757): one tfv_identity
# range (source, product, storm_id '') read backwards, LIMIT 1, never the view's DISTINCT
# ON (CLAUDE.md d091551). key_below is '<stamp>s': every key whose base is at or before the
# stamp sorts below it under C (the ledger's collation), r<n> included ('r' < 's'), and
# every later stamp above it. The base is cut off the key, so a correction's key is
# never taken for an issuance. The row served at the stamp is read through the view
# (DISPLAY_FILES_SQL), as every display object is.
OUTLOOK_STAMP_SQL = """
    SELECT substring(a.vintage_key FROM '^[0-9]{12}') AS stamp
      FROM tropical_file_vintage AS a
     WHERE a.source = 'display' AND a.product = %(product)s AND a.storm_id = ''
       AND a.status = 'banked' AND a.vintage_key < %(key_below)s
     ORDER BY a.vintage_key DESC
     LIMIT 1
"""

# Every banked member parquet of one storm, per cycle (vintage_key): one
# tfv_identity range per product. The bank's own derivations (source 'parsed').
MEMBERS_SQL = """
    SELECT v.source, v.product, v.vintage_key, v.r2_key, v.sha256, v.byte_size,
           v.issued_ts, v.meta, v.revision, v.corrected
      FROM tropical_file_vintage_in_force AS v
     WHERE v.source = 'parsed' AND v.product = ANY (%(products)s::text[])
       AND v.storm_id = %(storm_id)s
       AND (%(cycle)s::text IS NULL OR v.vintage_key = %(cycle)s)
     ORDER BY v.vintage_key, v.product
"""

# Every published member manifest of one storm, the copy in force per cycle
# (advisory_or_cycle), read as DISPLAY_FILES_SQL reads the advisory objects.
DISPLAY_MEMBERS_SQL = """
    SELECT 'display' AS source, v.product, v.advisory_or_cycle, v.vintage_key, v.revision,
           v.revision > 0 AS corrected, v.r2_key, v.sha256, v.byte_size, v.issued_ts,
           v.published_ts, v.meta
      FROM tropical_display_in_force AS v
     WHERE v.product = ANY (%(products)s::text[]) AND v.storm_id = %(storm_id)s
       AND (%(cycle)s::text IS NULL OR v.advisory_or_cycle = %(cycle)s)
     ORDER BY v.advisory_or_cycle, v.product
"""


def files_params(storm_id: str, vintage_key: str, pairs) -> dict:
    """FILES_SQL's parameters: the NHC products of (served name, NHC product) pairs."""
    return {"products": list(dict.fromkeys(p for _, p in pairs)), "storm_id": storm_id,
            "vintage_key": vintage_key}


def display_files_params(storm_id: str, advisory_or_cycle: str, pairs) -> dict:
    """DISPLAY_FILES_SQL's parameters: the display products of the same pairs."""
    return {"products": [display_product(n) for n, _ in pairs], "storm_id": storm_id,
            "advisory_or_cycle": advisory_or_cycle}


def outlook_stamp_params(name: str, at_or_before: Optional[str] = None) -> dict:
    """OUTLOOK_STAMP_SQL's parameters: the newest stamp of display:<name> at or before
    a stamp (default: the newest of all)."""
    return {"product": display_product(name), "key_below": (at_or_before or STAMP_MAX) + "s"}


def members_params(storm_id: str, cycle: Optional[str]) -> dict:
    return {"products": [p for _, p in ENSEMBLES], "storm_id": storm_id, "cycle": cycle}


def display_members_params(storm_id: str, cycle: Optional[str]) -> dict:
    return {"products": [members_product(e) for e, _ in ENSEMBLES], "storm_id": storm_id,
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


def _revision_of(r: dict) -> dict:
    """The object's own correction state. The outlook reads no NHC row, so it states no
    source_in_force."""
    return {"revision": r["revision"], "corrected": r["corrected"], "original": _original(r)}


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


# The label table's keys, the publisher's own names (pantry tropical/publish.py _toa_meta,
# scripts/withdraw_toa_d091756.py): served where the row carries them, never filled.
TOA_LABEL_KEYS = ("labels", "labels_null", "timezone", "withdrawal")


def _toa_object(name: str, r: dict, src: Optional[dict]) -> dict:
    """An arrival-time object: the cone's fields, and the label table its row carries,
    under the row's own keys and names (D-09-25-242 §1(d)). Nothing here reads, parses,
    renames or fills a label; a key the row does not carry is not served."""
    meta = r["meta"] or {}
    return {**_object(name, r, src),
            "advisory_in_object": meta.get("advisory_in_object"),
            **({"advisory_check": meta["advisory_check"]} if "advisory_check" in meta else {}),
            "labels": {k: meta[k] for k in TOA_LABEL_KEYS if k in meta}}


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


def _served_and_absent(pairs, rows, *, advisory, entry, basin, shape=None) -> tuple[list, list]:
    by = _by_product(rows)
    objects, absent = [], []
    for name, nhc_product in pairs:
        src = by.get((NHC_SOURCE, nhc_product))
        pub = by.get((DISPLAY_SOURCE, display_product(name)))
        if pub is not None:
            objects.append((shape or _object)(name, pub, src))
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
                         "time, never the string). Per product, the copy in force (published "
                         "objects: pantry 314's tropical_display_in_force; NHC's files: 294). "
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
                "absent": [], "toa": None, "wsp": None}
    else:
        objects, absent = _served_and_absent(ADVISORY_PRODUCTS, rows, advisory=advisory,
                                             entry=entry, basin=storm["basin"])
        tobj, tabs = _served_and_absent(TOA_PRODUCTS, rows, advisory=advisory, entry=entry,
                                        basin=storm["basin"], shape=_toa_object)
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
                "revision": max((o["revision"] for o in objects + tobj), default=0),
                "corrected": any(o["corrected"] for o in objects + tobj),
                "objects": objects, "absent": absent,
                "toa": {"objects": tobj, "absent": tabs}, "wsp": wsp, "absence": None}
    body.update({
        "advisory_rule": ADVISORY_DEFAULT_RULE,
        "wsp_rule": ("WSP is issued per synoptic cycle, basin-wide, not per storm: the cycle is "
                     "the advisory's synoptic time (its official forecast's init_ts) as "
                     "YYYYMMDDHH. Bands are the 5 km zip's contours (11 percentage bands); the "
                     "grid is the half-degree zip's points, value in percent, cells below 0.1 % "
                     "absent."),
        "revision_rule": ("revision and corrected are each object's own: the copy in force is "
                          "the highest revision published for the advisory or cycle (pantry "
                          "314's tropical_display_in_force; a correction NHC issued is its own "
                          "ledger row, key '<ADV>r<n>'), corrected = revision > 0, and "
                          "`original` is the revision-0 object where one is corrected, stated "
                          "even when its bytes equal the correction's (NHC re-posted the same "
                          "file). The top-level revision is the highest served; "
                          "source_in_force says whether the NHC file an object names is the "
                          "copy in force now."),
        "toa_rule": TOA_RULE,
        "index_rule": INDEX_RULE, "reasons": REASONS, "timestamps": _tr.TIMESTAMPS,
        "not_served": NOT_SERVED_INDEX, "nhc_terms": _tr.NHC_TERMS,
    })
    return body


TOA_RULE = ("toa holds the advisory's arrival-time objects (NHC's earliest reasonable and most "
            "likely arrival of tropical-storm-force winds), read as the cone is: per product the "
            "copy in force on pantry 314's tropical_display_in_force, revision / corrected / "
            "original each object's own, absences by the same reasons (not_issued on NHC's "
            "CurrentStorms refs toa_earliest_kmz / toa_mostlikely_kmz). `labels` is the label "
            "table the object's ledger row carries, under the row's own keys and names, served "
            "as banked and never parsed, inferred, renamed or filled here (D-09-25-242 §1(d)): "
            "labels {n, null, parsed, backwards, by_source, icon_bands, not_a_time} is the "
            "publisher's count verbatim; labels_null [{label, why}] the labels that are not a "
            "time; timezone the zone the KML states; withdrawal the ledger's note where the "
            "object was withdrawn and republished under its own key (D-09-25-236: ruling, "
            "withdrawn_vintage_id, withdrawn_sha256, withdrawn_fetch_ts). A key the row does "
            "not carry is not served. advisory_in_object is the advisory the KML carries, as "
            "banked (a string). The top-level revision counts these objects too.")


# ── /outlook ────────────────────────────────────────────────────────────────

OUTLOOK_REASONS = {
    "not_published": "the ledger holds no published display object for it",
}
STAMP_RULE = ("An outlook issuance is keyed by its 12-digit stamp YYYYMMDDHHMM (D-09-25-234; pantry "
              "migration 316): the row's vintage_key and tropical_display_in_force's "
              "advisory_or_cycle. `stamp` is the graphical issuance's (the gtwo zip's, which "
              "two_areas and two_points share); without one, the newest published. Two outlooks "
              "of one hour are two issuances, and r<n> is a correction NHC issued over a stamp, "
              "never an issuance: the copy in force at the stamp is served, revision stated.")
PAIRING_RULE = ("The text is paired to the graphics by stamp: the basin's outlook text with the "
                "newest stamp at or before the graphics' (the text in force when NHC cut the "
                "zip). Each text has its own stamp (its WMO heading's DDHHMM), so the two "
                "stamps may differ; each object prints its own. issued_utc is the text's "
                "nominal synoptic time as NHC wrote it, transmitted_utc its WMO time: the "
                "pairing reads neither.")
BASIN_RULE = ("basin al is served NHC's Atlantic outlook (two_text_at); ep and cp are served NHC's "
              "East Pacific outlook (two_text_ep), whose own heading, basin_heading, states its "
              "reach; the bank holds no CPHC outlook text. The graphics are one gtwo zip for "
              "every basin: each feature carries its own BASIN field, in the object at url.")


def _two_graphics_obj(name: str, r: dict) -> dict:
    meta = r["meta"] or {}
    return {"product": name, "url": display_url(r["r2_key"]), "sha256": r["sha256"],
            "bytes": r["byte_size"], **{k: meta.get(k) for k in META_TWO_GRAPHICS},
            **_revision_of(r)}


def _two_text_obj(name: str, r: dict) -> dict:
    meta = r["meta"] or {}
    return {"product": name, "text_basin": name.rsplit("_", 1)[1], "url": display_url(r["r2_key"]),
            "sha256": r["sha256"], "bytes": r["byte_size"],
            "basin_heading": meta.get("basin"), **{k: meta.get(k) for k in META_TWO_TEXT},
            **_revision_of(r), "absence": None}


def build_outlook(*, basin: str, stamp: Optional[str], requested: Optional[str], rows: list,
                  text_name: str, text_stamp: Optional[str], text_rows: list) -> dict:
    """GET /outlook. `stamp` is the graphical issuance served (the requested one, or the
    newest); `rows` the display rows at it; `text_stamp` the basin's text paired to it
    (None: none published at or before it) and `text_rows` the rows at that stamp."""
    by = _by_product(rows)
    objects, absent = [], []
    for name in OUTLOOK_GRAPHICS:
        pub = by.get((DISPLAY_SOURCE, display_product(name)))
        if pub is not None:
            objects.append(_two_graphics_obj(name, pub))
        else:
            absent.append({"product": name, "reason": "not_published",
                           "detail": (f"the ledger holds no published {display_product(name)} at "
                                      f"stamp {stamp}" if stamp else
                                      f"the ledger holds no published {display_product(name)}")})
    tpub = _by_product(text_rows).get((DISPLAY_SOURCE, display_product(text_name)))
    if tpub is not None:
        text = _two_text_obj(text_name, tpub)
    else:
        text = {"product": text_name, "text_basin": text_name.rsplit("_", 1)[1], "url": None,
                "sha256": None, "bytes": None, "basin_heading": None,
                **{k: None for k in META_TWO_TEXT}, "revision": None, "corrected": None,
                "original": None,
                "absence": {"reason": "not_published",
                            "detail": (f"the ledger holds no published {display_product(text_name)} "
                                       f"at or before stamp {stamp}" if stamp else
                                       f"the ledger holds no published {display_product(text_name)}")}}
    served = objects + ([text] if tpub is not None else [])
    head = by.get((DISPLAY_SOURCE, display_product("two_areas")))
    return {
        "basin": basin, "stamp": stamp, "stamp_requested": requested is not None,
        "issued_ts": _ts(head["issued_ts"]) if head else None,
        "revision": max((o["revision"] for o in served), default=0),
        "corrected": any(o["corrected"] for o in served),
        "graphics": {"stamp": stamp, "objects": objects, "absent": absent},
        "text": text,
        "absence": None if stamp else _absence(
            "not_published", "the ledger holds no published display:two_areas: no outlook issuance "
                             "to serve"),
        "stamp_rule": STAMP_RULE, "pairing_rule": PAIRING_RULE, "basin_rule": BASIN_RULE,
        "revision_rule": ("revision and corrected are each object's own, as on /products: the "
                          "copy in force at a stamp is the highest revision published over it "
                          "(pantry 314's tropical_display_in_force; key '<STAMP>r<n>'), and "
                          "`original` is the revision-0 object where one is corrected. The "
                          "top-level revision is the highest served."),
        "index_rule": INDEX_RULE, "reasons": OUTLOOK_REASONS, "timestamps": _tr.TIMESTAMPS,
        "not_served": NOT_SERVED_INDEX, "nhc_terms": _tr.NHC_TERMS,
    }


# ── /text ───────────────────────────────────────────────────────────────────

def _text_obj(name: str, r: dict, src: Optional[dict]) -> dict:
    meta = r["meta"] or {}
    return {"product": name, "url": display_url(r["r2_key"]), "sha256": r["sha256"],
            "bytes": r["byte_size"], "issued_utc": meta.get("issued_utc"),
            "transmitted_utc": meta.get("transmitted_utc"), "forecaster": meta.get("forecaster"),
            "title": meta.get("title"), "n_forecast_rows": meta.get("n_forecast_rows"),
            "sections": [{"kind": k} for k in (meta.get("section_kinds") or [])],
            "source_r2_key": meta.get("source_r2_key"), "source_sha256": meta.get("source_sha256"),
            **_provenance(r, src), "absence": None}


def build_text(*, storm: dict, advisory: Optional[str], adv: Optional[dict],
               requested: Optional[str], entry: Optional[dict], rows: list) -> dict:
    """GET /text. One advisory's TCD and TCP as published, sections listed."""
    if advisory is None:
        body = {**no_advisory(storm), "revision": 0, "corrected": False, "products": {}}
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
                                  "title": None, "n_forecast_rows": None, "sections": [], "source_r2_key": None, "source_sha256": None,
                                  "revision": None, "corrected": None, "original": None,
                                  "source_in_force": None,
                                  "absence": {"reason": a["reason"], "detail": a["detail"]}}
        served = [o for o in products.values() if o["absence"] is None]
        body = {**_advisory_head(storm, advisory, adv, requested, entry),
                "revision": max((o["revision"] for o in served), default=0),
                "corrected": any(o["corrected"] for o in served),
                "products": products, "absence": None}
    body.update({
        "advisory_rule": ADVISORY_DEFAULT_RULE,
        "times_rule": ("Two times, and they differ: transmitted_utc is the WMO heading's DDHHMM "
                       "(when NHC transmitted the product), issued_utc the product's local "
                       "issuance line in UTC (the advisory's nominal time). The TCD's forecast "
                       "table counts from the advisory time, the bank's official track from the "
                       "synoptic time: never join them on tau."),
        "revision_rule": ("revision and corrected are each product's own, as on /products: the "
                          "copy in force is the highest revision published (a correction NHC "
                          "issued is its own ledger row, key '<ADV>r<n>'), and `original` is "
                          "the revision-0 object where one is corrected. The top-level revision "
                          "is the highest served."),
        "sections_rule": ("sections are the publisher's section_kinds, in NHC's order, each "
                          "{kind} as the parser names it. The ledger carries no section titles: "
                          "they, and the text, are in the object at url. title is the "
                          "product's own heading as the parse read it; n_forecast_rows the "
                          "rows of its forecast table (0 for a TCP)."),
        "index_rule": INDEX_RULE, "reasons": REASONS, "timestamps": _tr.TIMESTAMPS,
        "not_served": NOT_SERVED_INDEX, "nhc_terms": _tr.NHC_TERMS,
    })
    return body


# ── /members ────────────────────────────────────────────────────────────────

MEMBERS_RULE = ("A published ensemble is a display-bucket manifest (schema tropics.members.v1, "
                "recon d091737 §4) in the ledger as product 'display:members:<ensemble>', "
                "the cycle its advisory_or_cycle, the copy in force per cycle "
                "(tropical_display_in_force). n_members, min_members and taus are the manifest's own "
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
            published[(r["advisory_or_cycle"], published_of[r["product"]])] = r
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
