"""The Tropics page — d091673 (API half), read from the tropical bank (pantry d091664).

    GET /api/weather/tropics/storms?scope=active|recent|all
                                        which storms the bank holds, where each is now
                                        and how strong, its newest advisory, and the
                                        newest successful poll
    GET /api/weather/tropics/storm?storm_id=
                                        one storm: identity and aliases, the observed
                                        track, the newest official forecast with radii,
                                        and every model cycle held with its sources
    GET /api/weather/tropics/tracks?storm_id=&n=
                                        the newest n model cycles (1-8, default 4),
                                        oldest first, one series per source, the
                                        official forecast at each cycle: the spaghetti
                                        plot and its run-by-run player in one body
    GET /api/weather/tropics/odds?storm_id=
                                        the newest issuance of NHC's wind speed
                                        probabilities at its named places

READ-ONLY. The bank owns every table read here (pantry migration 291; the
freshness arm is 293's): tropical_storms, tropical_track_points,
tropical_place_odds, and the heartbeat rows and dataset row behind the
nhc_storms_current arm. This module holds the SQL and the pure shaping; main.py
holds the routes, the memo and the statement timeout (D-09-25-75).

THE RULINGS (spec d091673) and where each is held:

  1. SERVE WHAT THE BANK STATES. Nothing is differenced, smoothed or
     interpolated. A stored `source` becomes a printed label in ONE place,
     SOURCES below, through `source_meta()`; a source not in it is served with
     UNKNOWN_SOURCE_LABEL, never mapped to a known one. The only value the API
     rewrites is longitude, and only a stored east-positive one (lon > 180), as
     the spec requires: see `signed_lon`.
  2. STORM IDENTITY IS THE BANK'S. Every read is keyed by tropical_storms'
     storm_id (NHC's id). No read joins on a name; `names` is served as the
     bank stores it.
  3. OFFICIAL AND DETERMINISTIC TRACKS ONLY. The bank's CHECK already pins
     tropical_track_points.source to 28 names with no member among them; the
     shaper refuses any member-shaped source by name anyway (MEMBER_SOURCE),
     and every body says where the members are (NOT_SERVED).
  4. AN ABSENCE IS STATED WITH A REASON. No storms is a 200 with the newest
     heartbeat; a known storm with an empty block is a 200 with that block's
     `absence`; an unknown storm is a 404.
  5. NO ROUND TRIP ON THE PLAYER'S STEP PATH. /tracks carries every cycle the
     player steps through, each with its official forecast.
  6. ECMWF-DERIVED TRACKS CARRY CC BY 4.0. Every series whose source SOURCES
     marks `ecmwf` carries ECMWF_NOTICE on itself.
  7. REPO RULES. Each statement names one storm and walks an index
     (uniq_tropical_track_points, ttp_storm_init, uniq_tropical_place_odds,
     tfv_heartbeat, the storms PK); newest-row reads are one LATERAL ... LIMIT 1
     per (storm, source) (d091551); single-flight memo; SET LOCAL
     statement_timeout. Plans: docs/receipts/tropics-api-d091673/plans.md.

WHAT THE BANK STORES, MEASURED 2026-10-09 (docs/handback_2026_10_09_tropics_api.md §1):

  * lon: signed, negative west, in every row (min -143.1, max 3.2; 0 rows
    above 180). The parsers write negative west; the CHECK still admits
    -180..360, so the API guards the east-positive case.
  * advisory: the bank normalises NHC's spellings ('008a', '008A', '8') to
    'NNN' or 'NNNA' (pantry tropical/nhc.py norm_adv). It is served verbatim and
    never sorted: an intermediate ('012A') carries its parent's ('012') init_ts
    and the same forecast points from tau 12 on; only its own position differs
    (tau 6 against the parent's tau 3). Advisories are ordered by init_ts, then
    by the valid time of the advisory's own (first) point.
  * valid_ts: the bank corrected NHC's 3 h-late forecast-radii VALIDTIME. Its
    CHECK ttp_valid_is_init_plus_tau holds valid_ts = init_ts + tau h on every
    row, and the parser never reads VALIDTIME for tau > 0. Served as stored.
  * radii: {"34": [ne, se, sw, nw], "50": [...], "64": [...]} nautical miles, as
    stored; `{}` where the source gave none.
  * IVCN: every one of its rows is at 0 deg, 0 deg (ATCF's placeholder: it is an
    intensity consensus with no track). Served as stored, marked `track: false`.
  * place_id: NHC's own printed name ("PANAMA CITY FL", "GFAM 290N 850W").
    Nothing the API can read holds a place's coordinates (STOP-P).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

# ── parameters ──────────────────────────────────────────────────────────────

SCOPES = ("active", "recent", "all")
SCOPE_DEFAULT = "active"
RECENT_DAYS = 7
TRACKS_N_DEFAULT = 4
TRACKS_N_MAX = 8
STORM_ID = re.compile(r"^(al|ep|cp)[0-9]{2}[0-9]{4}$")        # migration 291's CHECK


def parse_scope(raw: Optional[str]) -> str:
    if raw is None or raw == "":
        return SCOPE_DEFAULT
    if raw not in SCOPES:
        raise ValueError(f"scope must be one of {list(SCOPES)}, got {raw!r}")
    return raw


def parse_storm_id(raw: Optional[str]) -> str:
    """NHC's lower-case id as the bank keys it, or ValueError. Never normalised:
    an id the bank could not hold is a bad parameter, not a guess."""
    if raw is None or raw == "":
        raise ValueError("storm_id is required: NHC's id as the bank keys it, e.g. al092026")
    if not STORM_ID.match(raw):
        raise ValueError(f"storm_id must match {STORM_ID.pattern} (e.g. al092026), got {raw!r}")
    return raw


def parse_tracks_n(raw: Optional[str]) -> int:
    """n or ValueError. Above TRACKS_N_MAX is refused, never trimmed."""
    if raw is None or raw == "":
        return TRACKS_N_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        raise ValueError(f"n must be an integer 1-{TRACKS_N_MAX}, got {raw!r}")
    if not 1 <= n <= TRACKS_N_MAX:
        raise ValueError(f"n must be 1-{TRACKS_N_MAX} model cycles, got {n}")
    return n


# ── the one label map (rule 1) ──────────────────────────────────────────────
#
# role:  observed    the storm's path as analysed after the fact (b-deck)
#        operational the operational analysis at each cycle (tcvitals)
#        official    NHC's forecast
#        model       one model's deterministic track
#        ensemble_mean  the mean of an ensemble (not a member)
#        consensus   an average of other aids' tracks
#        intensity_consensus  an average of intensities only: no track
#        baseline    climatology and persistence: the no-skill yardstick
# adeck: "late" = the model's own run at its own init, in the A-deck as NHC
#        received it; "early" = available at the synoptic time (consensus and
#        baselines, built from the previous cycle's interpolated aids). The bank
#        holds no interpolated ("I"/"2") version of any model (pantry
#        tropical/points.py NEON_ADECK_AIDS), so no model appears twice as
#        early and late. Measured 2026-10-09: the 18Z cycle held only the seven
#        early aids, the late models stopped at 12Z.
# same_model_as: the bank holds the same model through a second feed. The two
#        are NOT the same numbers (measured: HFSA vs hafs_a, 466 points at
#        equal init and tau, 32 identical positions, mean 0.97 deg apart), so
#        both are served, each under its own label.

ECMWF = "ecmwf"
ECMWF_POSSIBLE = "ecmwf_in_consensus"

SOURCES = {
    "nhc_official": {"label": "NHC official forecast", "role": "official", "feed": "NHC advisory GIS"},
    "best_track": {"label": "NHC best track (preliminary)", "role": "observed", "feed": "ATCF b-deck"},
    "tcvitals": {"label": "TCVitals (operational analysis)", "role": "operational", "feed": "NOAA GFS tcvitals"},
    "ecmwf_ifs": {"label": "ECMWF IFS", "role": "model", "feed": "ECMWF open data tf BUFR", "ecmwf": ECMWF},
    "ecmwf_aifs": {"label": "ECMWF AIFS", "role": "model", "feed": "ECMWF open data tf BUFR", "ecmwf": ECMWF},
    "hafs_a": {"label": "HAFS-A (NOAA tracker file)", "role": "model", "feed": "NOAA HAFS atcfunix (AWS)",
               "same_model_as": "atcf:HFSA"},
    "hafs_b": {"label": "HAFS-B (NOAA tracker file)", "role": "model", "feed": "NOAA HAFS atcfunix (AWS)",
               "same_model_as": "atcf:HFSB"},
    "atcf:OFCL": {"label": "NHC official (A-deck OFCL)", "role": "official", "feed": "ATCF a-deck",
                  "adeck": "late", "same_model_as": "nhc_official"},
    "atcf:AVNO": {"label": "GFS (A-deck AVNO)", "role": "model", "feed": "ATCF a-deck", "adeck": "late"},
    "atcf:UKX": {"label": "UKMET, GFS tracker (A-deck UKX)", "role": "model", "feed": "ATCF a-deck", "adeck": "late"},
    "atcf:CMC": {"label": "Canadian GDPS (A-deck CMC)", "role": "model", "feed": "ATCF a-deck", "adeck": "late"},
    "atcf:NVGM": {"label": "NAVGEM (A-deck NVGM)", "role": "model", "feed": "ATCF a-deck", "adeck": "late",
                  "same_model_as": "atcf:NGX"},
    "atcf:NGX": {"label": "NAVGEM (A-deck NGX)", "role": "model", "feed": "ATCF a-deck", "adeck": "late",
                 "same_model_as": "atcf:NVGM"},
    "atcf:HFSA": {"label": "HAFS-A (A-deck HFSA)", "role": "model", "feed": "ATCF a-deck", "adeck": "late",
                  "same_model_as": "hafs_a"},
    "atcf:HFSB": {"label": "HAFS-B (A-deck HFSB)", "role": "model", "feed": "ATCF a-deck", "adeck": "late",
                  "same_model_as": "hafs_b"},
    "atcf:HWRF": {"label": "HWRF, legacy (A-deck HWRF)", "role": "model", "feed": "ATCF a-deck", "adeck": "late"},
    "atcf:HMON": {"label": "HMON, legacy (A-deck HMON)", "role": "model", "feed": "ATCF a-deck", "adeck": "late"},
    "atcf:CTCX": {"label": "COAMPS-TC (A-deck CTCX)", "role": "model", "feed": "ATCF a-deck", "adeck": "late"},
    "atcf:AEMN": {"label": "GEFS mean (A-deck AEMN)", "role": "ensemble_mean", "feed": "ATCF a-deck", "adeck": "late"},
    "atcf:CEMN": {"label": "Canadian ensemble mean (A-deck CEMN)", "role": "ensemble_mean", "feed": "ATCF a-deck",
                  "adeck": "late"},
    "atcf:GDMN": {"label": "Google DeepMind ensemble mean (A-deck GDMN)", "role": "ensemble_mean",
                  "feed": "ATCF a-deck", "adeck": "late"},
    "atcf:TVCN": {"label": "TVCN track consensus (A-deck)", "role": "consensus", "feed": "ATCF a-deck",
                  "adeck": "early", "ecmwf": ECMWF_POSSIBLE},
    "atcf:HCCA": {"label": "HCCA corrected consensus (A-deck)", "role": "consensus", "feed": "ATCF a-deck",
                  "adeck": "early", "ecmwf": ECMWF_POSSIBLE},
    "atcf:RVCN": {"label": "RVCN consensus (A-deck)", "role": "consensus", "feed": "ATCF a-deck",
                  "adeck": "early", "ecmwf": ECMWF_POSSIBLE},
    "atcf:IVCN": {"label": "IVCN intensity consensus (A-deck)", "role": "intensity_consensus",
                  "feed": "ATCF a-deck", "adeck": "early", "ecmwf": ECMWF_POSSIBLE, "track": False,
                  "track_note": "an intensity consensus forecasts vmax only; every IVCN row in the bank "
                                "is at 0 deg, 0 deg, ATCF's placeholder. Draw its intensity, never its "
                                "position."},
    "atcf:CLP5": {"label": "CLIPER5 baseline (A-deck CLP5)", "role": "baseline", "feed": "ATCF a-deck",
                  "adeck": "early"},
    "atcf:OCD5": {"label": "OCD5 baseline (A-deck OCD5)", "role": "baseline", "feed": "ATCF a-deck",
                  "adeck": "early"},
    "atcf:TCLP": {"label": "TCLP trajectory baseline (A-deck TCLP)", "role": "baseline", "feed": "ATCF a-deck",
                  "adeck": "early"},
}

UNKNOWN_SOURCE_LABEL = "unknown source: served as the bank states it"

# Sources that are never a series on /tracks: the observed and operational
# positions (on /storm), and the official forecast (it rides with each cycle).
NOT_A_SERIES = ("best_track", "tcvitals", "nhc_official")
OBSERVED = ("best_track", "tcvitals")
OFFICIAL = "nhc_official"

# Rule 3: an ensemble member's source, by name (migration 291's comment, the
# d091664 handback §1: ECMWF ENS / AIFS-ENS, GEFS AP01-AP30 + AC00, Canadian
# CP.., Google DeepMind F000-F049). Never served; named in `refused`.
MEMBER_SOURCE = re.compile(
    r"^(ecmwf_ens|ecmwf_aifs_ens|atcf:(AP[0-9]{2}|AC00|CP[0-9]{2}|CC00|F[0-9]{3}))$")


def source_meta(source: str) -> dict:
    """The printed label and role of a stored source: the ONE place a label is made."""
    m = SOURCES.get(source)
    if m is None:
        return {"source": source, "status": "unknown", "label": UNKNOWN_SOURCE_LABEL,
                "role": "unknown", "feed": None, "adeck": None, "same_model_as": None,
                "ecmwf": None, "track": True, "track_note": None}
    return {"source": source, "status": "known", "label": m["label"], "role": m["role"],
            "feed": m["feed"], "adeck": m.get("adeck"), "same_model_as": m.get("same_model_as"),
            "ecmwf": m.get("ecmwf"), "track": m.get("track", True),
            "track_note": m.get("track_note")}


# ── statements, rules, notices ──────────────────────────────────────────────

ECMWF_NOTICE = ("(c) {year} European Centre for Medium-Range Weather Forecasts (ECMWF). "
                "Source: www.ecmwf.int. Licence: CC BY 4.0 "
                "(https://creativecommons.org/licenses/by/4.0/). Modified: tracks decoded from "
                "ECMWF's open-data tropical cyclone BUFR, positions rounded to 0.01 deg, 10 m "
                "wind converted to knots, radii to nautical miles, matched to NHC storms by "
                "position.")
ECMWF_IN_CONSENSUS_NOTICE = ("This consensus aid may contain ECMWF content, which NHC's ATCF "
                             "NOTICE places under CC BY 4.0: (c) {year} European Centre for "
                             "Medium-Range Weather Forecasts (ECMWF). Source: www.ecmwf.int. "
                             "Licence: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).")
NHC_TERMS = ("NOAA/NHC products are public domain. They are served as the bank parsed them, "
             "are not presented as NHC's own official display, and imply no endorsement.")

LONGITUDE = {
    "served": "signed degrees, -180..180, east positive (lon_deg)",
    "bank_stores": "signed degrees, negative west, in every row measured 2026-10-09 "
                   "(min -143.1, max 3.2, none above 180); migration 291's CHECK admits -180..360",
    "rule": "a stored lon above 180 is served as lon - 360 (rounded to 1e-6 to drop float "
            "residue); every other lon is served as stored",
}
ADVISORY_RULE = ("advisory is served verbatim as the bank stores it (normalised by the bank to "
                 "NNN or NNNA). Advisories are ordered by init_ts (the synoptic time), then by the "
                 "valid time of the advisory's own position (its first point), never by the "
                 "string. An intermediate advisory (e.g. 012A) is issued 3 h after its parent "
                 "(012) under the same init_ts: it re-issues the parent's forecast with every "
                 "point from tau 12 on unchanged, and moves only the storm's own position (tau 6 "
                 "against the parent's tau 3).")
VALID_TS_RULE = ("valid_ts is served as the bank stores it, and the bank's CHECK holds valid_ts "
                 "= init_ts + tau_h on every row. The bank corrected NHC's forecast-radii "
                 "VALIDTIME (3 h late at every forecast tau): it dates each point from the "
                 "synoptic time and never reads VALIDTIME for tau > 0. The API corrects nothing.")
RADII_CONVENTION = ("radii_nm: {\"34\": [ne, se, sw, nw], \"50\": [...], \"64\": [...]}, the radius in "
                    "nautical miles of 34-, 50- and 64-kt winds in each quadrant, as the bank stores "
                    "it; {} where the source gave none.")
TIMESTAMPS = "ISO 8601, UTC, e.g. 2026-10-09T12:00:00+00:00"

NOT_SERVED = {
    "ensemble_members": (
        "ECMWF ENS (51 members), AIFS-ENS (52) and the A-deck's GEFS members AP01-AP30 and "
        "AC00 are not in Neon: the bank keeps every member as R2 parquet "
        "(tropical/tracks/{storm}/{cycle}/{source}.parquet), and tropical_track_points has "
        "no member column. This API reads no R2 per request."),
    "cone_and_warnings": (
        "NHC's cone and its watch and warning polygons are banked as files in R2 (the "
        "advisory's 5-day zip and cone/WW KMZ), not as rows. This API reads no R2."),
    "storms_before_2026": (
        "Nothing before 2026 is parsed into the bank; lane d091671 is building that "
        "backfill. A storm the bank never saw live is not here."),
    "model_scores": (
        "No track or intensity error is scored: the bank holds no score table for tracks, "
        "and every tropical_place_odds row is scored = false."),
    "place_coordinates": (
        "A place's coordinates: tropical_place_odds.place_id is NHC's printed name and no "
        "table the API can read holds its latitude or longitude (STOP-P)."),
}

POLL_FRONTIER_SOURCE = "tropical_file_vintage.fetch_ts (product = 'heartbeat': a successful poll, storms or none)"
POLL_RULE = ("ingestion_freshness's grade for nhc_storms_current (migration 293): MISSING with no "
             "heartbeat, STALE when the time since the newest heartbeat exceeds the dataset's "
             "stale_after_override, else FRESH; applied here to the same frontier, read off the "
             "heartbeat index (the view itself builds every dataset's arm, 280 ms)")


# The newest successful poll (tfv_heartbeat, one row) and the arm's ruler.
POLL_SQL = """
    SELECT hb.fetch_ts AS newest_heartbeat_ts,
           d.stale_after_override,
           d.lifecycle_status
      FROM (SELECT 1) AS one
      LEFT JOIN LATERAL (
            SELECT fetch_ts
              FROM tropical_file_vintage
             WHERE product = 'heartbeat'
             ORDER BY fetch_ts DESC
             LIMIT 1) AS hb ON true
      LEFT JOIN datasets AS d ON d.dataset_code = 'nhc_storms_current'
"""

# THE ROWS IN FORCE (spec d091682, ruling D-09-25-174, pantry migration 294).
# Every read of track points and place odds is of pantry's views, never the
# bare tables: per advisory, the rows of its highest revision only, so an
# advisory NHC corrected is read whole (a tau the correction dropped does not
# survive from the original) and two revisions of one advisory never mix. The
# bare tables are the history; no page route reads them. Each view row carries
# `revision`: 0 for the original, n for the n-th correction whose rows differ.
#
# Whether NHC corrected an advisory is the ledger's word, read from
# tropical_file_vintage_in_force (one row per banked file identity, the copy
# in force, with `corrected`): the official forecast is parsed from the
# advisory's 5-day zip and its forecast-radii zip (pantry
# ingesters/tropical_bank.py _official_points), an odds issuance from the PWS
# text (_pws_odds). A correction that parses to identical rows writes no row,
# so `corrected` can be true at revision 0 (handback d091682 §1).

# Storms in scope, each with its newest best-track fix and tcvitals analysis:
# one LATERAL ... LIMIT 1 per (storm, source) on uniq_tropical_track_points.
STORMS_SQL = """
    SELECT s.storm_id, s.basin, s.number, s.season, s.status, s.names, s.aliases,
           s.first_seen, s.last_seen, s.last_lat, s.last_lon, s.updated_ts,
           bt.init_ts AS bt_init_ts, bt.valid_ts AS bt_valid_ts, bt.lat AS bt_lat,
           bt.lon AS bt_lon, bt.vmax_kt AS bt_vmax_kt, bt.mslp_hpa AS bt_mslp_hpa,
           bt.stage AS bt_stage,
           tv.init_ts AS tv_init_ts, tv.valid_ts AS tv_valid_ts, tv.lat AS tv_lat,
           tv.lon AS tv_lon, tv.vmax_kt AS tv_vmax_kt, tv.mslp_hpa AS tv_mslp_hpa,
           tv.stage AS tv_stage
      FROM tropical_storms AS s
      LEFT JOIN LATERAL (
            SELECT init_ts, valid_ts, lat, lon, vmax_kt, mslp_hpa, stage
              FROM tropical_track_points_in_force
             WHERE storm_id = s.storm_id AND source = 'best_track'
             ORDER BY init_ts DESC, advisory DESC, tau DESC
             LIMIT 1) AS bt ON true
      LEFT JOIN LATERAL (
            SELECT init_ts, valid_ts, lat, lon, vmax_kt, mslp_hpa, stage
              FROM tropical_track_points_in_force
             WHERE storm_id = s.storm_id AND source = 'tcvitals'
             ORDER BY init_ts DESC, advisory DESC, tau DESC
             LIMIT 1) AS tv ON true
     WHERE (%(scope)s = 'all'
            OR (%(scope)s = 'active' AND s.status <> 'inactive')
            OR (%(scope)s = 'recent' AND s.last_seen >= now() - make_interval(days => %(recent_days)s)))
     ORDER BY s.storm_id
"""

# The official forecast at each storm's newest init: every advisory sharing
# that init (a regular one and its intermediate), all their points in force.
# One LATERAL per storm for the newest init, one index range for its rows, and
# per advisory the ledger's word on its two source files (the copy in force of
# each: one probe of tfv_identity).
OFFICIAL_NEWEST_SQL = """
    SELECT p.storm_id, p.advisory, p.init_ts, p.tau, p.valid_ts, p.lat, p.lon,
           p.vmax_kt, p.mslp_hpa, p.radii, p.stage, p.revision, f.corrected
      FROM unnest(%(storm_ids)s::text[]) AS u(storm_id)
      CROSS JOIN LATERAL (
            SELECT init_ts
              FROM tropical_track_points_in_force
             WHERE storm_id = u.storm_id AND source = 'nhc_official'
             ORDER BY init_ts DESC
             LIMIT 1) AS m
      JOIN tropical_track_points_in_force AS p
        ON p.storm_id = u.storm_id AND p.source = 'nhc_official' AND p.init_ts = m.init_ts
      LEFT JOIN LATERAL (
            SELECT bool_or(v.corrected) AS corrected
              FROM tropical_file_vintage_in_force AS v
             WHERE v.source = 'nhc' AND v.product IN ('fcst_5day_zip', 'fcst_radii_zip')
               AND v.storm_id = p.storm_id AND v.vintage_key = p.advisory) AS f ON true
     ORDER BY p.storm_id, p.advisory, p.tau
"""

STORM_SQL = """
    SELECT storm_id, basin, number, season, status, names, aliases,
           first_seen, last_seen, last_lat, last_lon, updated_ts
      FROM tropical_storms
     WHERE storm_id = %(storm_id)s
"""

# The observed track: every best-track fix and tcvitals analysis, two index
# ranges of uniq_tropical_track_points.
OBSERVED_SQL = """
    SELECT source, init_ts, advisory, tau, valid_ts, lat, lon, vmax_kt, mslp_hpa, radii, stage
      FROM tropical_track_points_in_force
     WHERE storm_id = %(storm_id)s AND source IN ('best_track', 'tcvitals')
     ORDER BY source, valid_ts, init_ts, tau
"""

# Every cycle the storm holds, with its sources: a census, no points. Driven by
# the same loose index scan as TRACKS_SQL (one probe per cycle down
# ttp_storm_init), then one index range per cycle. The plain GROUP BY over the
# storm's rows is a seq scan of the whole table on Neon (Rachel is 64 % of it
# today), which would grow with every storm the backfill adds; this grows with
# the storm alone (plans.md §CYCLES). Every advisory has rows in force, so the
# probes find the same inits on the view as on the table; the counts are of
# the rows in force.
CYCLES_SQL = """
    WITH RECURSIVE cyc AS (
        (SELECT init_ts
           FROM tropical_track_points_in_force
          WHERE storm_id = %(storm_id)s
            AND source NOT IN ('best_track', 'tcvitals', 'nhc_official')
          ORDER BY init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT t.init_ts
                  FROM tropical_track_points_in_force AS t
                 WHERE t.storm_id = %(storm_id)s
                   AND t.source NOT IN ('best_track', 'tcvitals', 'nhc_official')
                   AND t.init_ts < cyc.init_ts
                 ORDER BY t.init_ts DESC
                 LIMIT 1)
          FROM cyc
         WHERE cyc.init_ts IS NOT NULL
    )
    SELECT c.init_ts, g.source, g.n_points, g.max_tau
      FROM cyc AS c
     CROSS JOIN LATERAL (
            SELECT source, count(*) AS n_points, max(tau) AS max_tau
              FROM tropical_track_points_in_force
             WHERE storm_id = %(storm_id)s AND init_ts = c.init_ts
               AND source NOT IN ('best_track', 'tcvitals', 'nhc_official')
             GROUP BY source) AS g
     WHERE c.init_ts IS NOT NULL
     ORDER BY c.init_ts, g.source
"""

# The newest n model cycles (a loose index scan down ttp_storm_init: one probe
# per cycle, never the storm's whole history), then every point in force of
# those cycles, official included, on the same index; for each official
# advisory, the ledger's word on its two source files.
TRACKS_SQL = """
    WITH RECURSIVE cyc AS (
        (SELECT init_ts
           FROM tropical_track_points_in_force
          WHERE storm_id = %(storm_id)s
            AND source NOT IN ('best_track', 'tcvitals', 'nhc_official')
          ORDER BY init_ts DESC
          LIMIT 1)
        UNION ALL
        SELECT (SELECT t.init_ts
                  FROM tropical_track_points_in_force AS t
                 WHERE t.storm_id = %(storm_id)s
                   AND t.source NOT IN ('best_track', 'tcvitals', 'nhc_official')
                   AND t.init_ts < cyc.init_ts
                 ORDER BY t.init_ts DESC
                 LIMIT 1)
          FROM cyc
         WHERE cyc.init_ts IS NOT NULL
    ), newest AS (
        SELECT init_ts FROM cyc WHERE init_ts IS NOT NULL LIMIT %(n)s
    ), pts AS (
        SELECT p.source, p.init_ts, p.advisory, p.tau, p.valid_ts, p.lat, p.lon,
               p.vmax_kt, p.mslp_hpa, p.stage, p.revision
          FROM tropical_track_points_in_force AS p
         WHERE p.storm_id = %(storm_id)s
           AND p.init_ts = ANY (ARRAY(SELECT init_ts FROM newest))
           AND p.source NOT IN ('best_track', 'tcvitals')
    ), official_files AS (
        SELECT a.advisory, f.corrected
          FROM (SELECT DISTINCT advisory FROM pts WHERE source = 'nhc_official') AS a
          CROSS JOIN LATERAL (
                SELECT bool_or(v.corrected) AS corrected
                  FROM tropical_file_vintage_in_force AS v
                 WHERE v.source = 'nhc' AND v.product IN ('fcst_5day_zip', 'fcst_radii_zip')
                   AND v.storm_id = %(storm_id)s AND v.vintage_key = a.advisory) AS f
    )
    SELECT pts.*, o.corrected
      FROM pts
      LEFT JOIN official_files AS o
        ON pts.source = 'nhc_official' AND o.advisory = pts.advisory
     ORDER BY pts.init_ts, pts.source, pts.advisory, pts.tau
"""

# The newest NHC PWS issuance in force for one storm: one LIMIT 1 on
# uniq_tropical_place_odds' (storm_id, source, issued_ts) prefix, then that
# issuance's rows in force in the bank's insertion order (NHC's printed
# order), and the ledger's word on the PWS text it was parsed from.
ODDS_SQL = """
    SELECT o.issued_ts, o.advisory, o.place_id, o.threshold_kt, o.radius_km, o.window_h,
           o.kind, o.value, o.below_1pct, o.scored, o.odds_id, o.revision, f.corrected
      FROM (SELECT issued_ts, advisory
              FROM tropical_place_odds_in_force
             WHERE storm_id = %(storm_id)s AND source = 'nhc_pws'
             ORDER BY issued_ts DESC
             LIMIT 1) AS m
      LEFT JOIN LATERAL (
            SELECT bool_or(v.corrected) AS corrected
              FROM tropical_file_vintage_in_force AS v
             WHERE v.source = 'nhc' AND v.product = 'pws'
               AND v.storm_id = %(storm_id)s AND v.vintage_key = m.advisory) AS f ON true
      JOIN tropical_place_odds_in_force AS o
        ON o.storm_id = %(storm_id)s AND o.source = 'nhc_pws' AND o.issued_ts = m.issued_ts
     ORDER BY o.odds_id
"""


# ── small helpers ───────────────────────────────────────────────────────────

def _ts(v) -> Optional[str]:
    """The repo's one format, in UTC whatever the session's TimeZone."""
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v.astimezone(timezone.utc).isoformat()


def _f(v) -> Optional[float]:
    return float(v) if v is not None else None


def signed_lon(lon) -> Optional[float]:
    """Rule of shape: served signed, -180..180. Only a stored east-positive
    value (above 180) is rewritten; every other value is the bank's own."""
    if lon is None:
        return None
    lon = float(lon)
    if lon > 180.0:
        return round(lon - 360.0, 6)
    return lon


def _absence(reason: str, detail: str) -> dict:
    return {"reason": reason, "detail": detail}


def _ecmwf_notice(kind: Optional[str], year: int) -> Optional[str]:
    if kind == ECMWF:
        return ECMWF_NOTICE.format(year=year)
    if kind == ECMWF_POSSIBLE:
        return ECMWF_IN_CONSENSUS_NOTICE.format(year=year)
    return None


def _point(r: dict, *, radii: bool = False, stage: bool = False) -> dict:
    out = {"tau_h": r["tau"], "valid_ts": _ts(r["valid_ts"]), "lat_deg": _f(r["lat"]),
           "lon_deg": signed_lon(r["lon"]), "vmax_kt": _f(r["vmax_kt"]),
           "mslp_hpa": _f(r["mslp_hpa"])}
    if stage:
        out["stage"] = r.get("stage")
    if radii:
        out["radii_nm"] = r.get("radii") if r.get("radii") is not None else {}
    return out


def _common() -> dict:
    return {"timestamps": TIMESTAMPS, "longitude": LONGITUDE, "not_served": NOT_SERVED,
            "nhc_terms": NHC_TERMS}


# ── identity ────────────────────────────────────────────────────────────────

def current_name(names) -> Optional[dict]:
    """The bank's newest name entry (greatest `to`, then `from`), as stored.
    Never used to find a storm: identity is storm_id."""
    if not names:
        return None
    return max(names, key=lambda e: (str(e.get("to") or ""), str(e.get("from") or "")))


def identity(s: dict) -> dict:
    cur = current_name(s["names"])
    return {
        "storm_id": s["storm_id"], "basin": s["basin"], "number": s["number"],
        "season": s["season"], "status": s["status"],
        "name": cur["name"] if cur else None,
        "name_absence": None if cur else _absence(
            "no_name", "the bank holds no name for this storm (an invest has none)"),
        "names": s["names"], "aliases": s["aliases"],
        "first_seen_ts": _ts(s["first_seen"]), "last_seen_ts": _ts(s["last_seen"]),
        "book_position": {
            "ts": _ts(s["last_seen"]), "lat_deg": _f(s["last_lat"]),
            "lon_deg": signed_lon(s["last_lon"]),
            "source": "tropical_storms.last_lat/last_lon: the storm book's last sighting "
                      "(CurrentStorms, tcvitals or the b-deck, whichever came last); no intensity",
        },
    }


# ── the official forecast ───────────────────────────────────────────────────

def correction(rows: list) -> dict:
    """`corrected` and `revision` of one advisory (or one odds issuance), from
    its rows in force. revision is the rows' own (the view serves one revision
    per advisory); corrected is the ledger's word on the files they were
    parsed from (tropical_file_vintage_in_force.corrected), never inferred from
    the numbers: NHC can correct an advisory without changing one."""
    return {"corrected": any(r.get("corrected") is True for r in rows),
            "revision": max((r.get("revision") or 0 for r in rows), default=0)}


def advisories(rows: list) -> list:
    """Official rows grouped by advisory, ordered by init_ts, then by the valid
    time of each advisory's own (first) point — never by the advisory string."""
    by = {}
    for r in rows:
        by.setdefault((r["init_ts"], r["advisory"]), []).append(r)
    out = []
    for (init, adv), pts in by.items():
        pts = sorted(pts, key=lambda r: r["tau"])
        out.append({"advisory": adv, "init_ts": init, "position_valid_ts": pts[0]["valid_ts"],
                    "position_tau_h": pts[0]["tau"], **correction(pts), "points": pts})
    out.sort(key=lambda a: (a["init_ts"], a["position_valid_ts"]))
    return out


def _advisory_obj(a: dict, *, radii: bool) -> dict:
    return {"advisory": a["advisory"], "init_ts": _ts(a["init_ts"]),
            "position_valid_ts": _ts(a["position_valid_ts"]),
            "position_tau_h": a["position_tau_h"],
            "corrected": a["corrected"], "revision": a["revision"],
            "points": [_point(r, radii=radii, stage=True) for r in a["points"]]}


def newest_official(rows: list) -> Optional[dict]:
    advs = advisories(rows)
    return advs[-1] if advs else None


# ── /storms ─────────────────────────────────────────────────────────────────

POSITION_PRECEDENCE = ("best_track", "nhc_official", "tcvitals")


def grade_poll(poll: Optional[dict], now: datetime) -> dict:
    """ingestion_freshness's CASE for nhc_storms_current, on the same frontier."""
    hb = poll.get("newest_heartbeat_ts") if poll else None
    stale_after = poll.get("stale_after_override") if poll else None
    if hb is None:
        status = "MISSING"
    elif stale_after is None:
        status = None
    elif now - hb > stale_after:
        status = "STALE"
    else:
        status = "FRESH"
    return {
        "newest_heartbeat_ts": _ts(hb),
        "freshness": {
            "status": status, "dataset": "nhc_storms_current",
            "stale_after_h": stale_after.total_seconds() / 3600.0 if stale_after is not None else None,
            "age_s": round((now - hb).total_seconds(), 1) if hb is not None else None,
            "graded_at": _ts(now), "frontier_source": POLL_FRONTIER_SOURCE, "rule": POLL_RULE,
            "absence": None if stale_after is not None or hb is None else _absence(
                "no_stale_after_override",
                "the dataset row carries no stale_after_override, so the arm has no ruler"),
        },
    }


def _candidate(prefix: str, s: dict, source: str) -> Optional[dict]:
    if s.get(f"{prefix}_valid_ts") is None:
        return None
    return {"source": source, "label": source_meta(source)["label"],
            "valid_ts": s[f"{prefix}_valid_ts"], "lat": s[f"{prefix}_lat"],
            "lon": s[f"{prefix}_lon"], "vmax_kt": s[f"{prefix}_vmax_kt"],
            "mslp_hpa": s[f"{prefix}_mslp_hpa"], "stage": s[f"{prefix}_stage"],
            "advisory": None}


def _position_obj(c: dict) -> dict:
    return {"source": c["source"], "label": c["label"], "valid_ts": _ts(c["valid_ts"]),
            "lat_deg": _f(c["lat"]), "lon_deg": signed_lon(c["lon"]),
            "vmax_kt": _f(c["vmax_kt"]), "mslp_hpa": _f(c["mslp_hpa"]), "stage": c["stage"],
            "advisory": c["advisory"]}


def storm_summary(s: dict, official_rows: list) -> dict:
    out = identity(s)
    cands = [c for c in (_candidate("bt", s, "best_track"), _candidate("tv", s, "tcvitals")) if c]
    adv = newest_official(official_rows)
    if adv is not None:
        p = adv["points"][0]
        cands.append({"source": OFFICIAL, "label": source_meta(OFFICIAL)["label"],
                      "valid_ts": p["valid_ts"], "lat": p["lat"], "lon": p["lon"],
                      "vmax_kt": p["vmax_kt"], "mslp_hpa": p["mslp_hpa"], "stage": p["stage"],
                      "advisory": adv["advisory"]})
    rank = {src: i for i, src in enumerate(POSITION_PRECEDENCE)}
    cands.sort(key=lambda c: (c["valid_ts"], -rank[c["source"]]))
    out["newest_position"] = _position_obj(cands[-1]) if cands else None
    out["newest_position_absence"] = None if cands else _absence(
        "no_position", "the bank holds no best-track fix, tcvitals analysis or official "
                       "position for this storm")
    out["positions"] = [_position_obj(c) for c in sorted(
        cands, key=lambda c: rank[c["source"]])]
    out["newest_advisory"] = None if adv is None else {
        "advisory": adv["advisory"], "init_ts": _ts(adv["init_ts"]),
        "position_valid_ts": _ts(adv["position_valid_ts"]),
        "position_tau_h": adv["position_tau_h"],
        "corrected": adv["corrected"], "revision": adv["revision"]}
    out["newest_advisory_absence"] = None if adv is not None else _absence(
        "no_official_forecast", "the bank holds no NHC official forecast for this storm")
    return out


def build_storms(*, scope: str, poll: Optional[dict], storm_rows: list, official_rows: list,
                 now: datetime) -> dict:
    """GET /storms. Every storm in scope, its newest position and advisory, the poll."""
    by_storm = {}
    for r in official_rows:
        by_storm.setdefault(r["storm_id"], []).append(r)
    storms = [storm_summary(s, by_storm.get(s["storm_id"], [])) for s in storm_rows]
    p = grade_poll(poll, now)
    body = {
        "scope": scope,
        "scope_rule": {"active": "every storm whose bank status is not inactive (active and invest)",
                       "recent": f"every storm last seen in the last {RECENT_DAYS} days, any status",
                       "all": "every storm the bank holds"}[scope],
        "count": len(storms),
        "storms": storms,
        "poll": p,
        "position_rule": ("newest_position is the latest by valid_ts of the newest best-track "
                          "fix, the newest tcvitals analysis and the newest official "
                          "advisory's own position; on a tie, best track, then official, then "
                          "tcvitals. `positions` lists all three as read."),
        "advisory_rule": ADVISORY_RULE,
        "absence": None,
        **_common(),
    }
    if not storms:
        hb = p["newest_heartbeat_ts"]
        body["absence"] = _absence(
            "no_storms_in_scope",
            f"the bank holds no storm in scope '{scope}'. "
            + (f"The newest successful poll of NHC's CurrentStorms was {hb} "
               f"({p['freshness']['status']})." if hb else
               "The bank holds no successful poll at all (freshness MISSING)."))
    return body


# ── /storm ──────────────────────────────────────────────────────────────────

def build_storm(*, storm: dict, observed_rows: list, official_rows: list,
                cycle_rows: list) -> dict:
    """GET /storm. Identity, the observed track, the newest official, every cycle."""
    obs = {}
    for src in OBSERVED:
        rows = sorted((r for r in observed_rows if r["source"] == src),
                      key=lambda r: (r["valid_ts"], r["init_ts"], r["tau"]))
        m = source_meta(src)
        obs[src] = {
            "source": src, "label": m["label"], "role": m["role"],
            "operational": src == "tcvitals",
            "points": [_point(r, radii=True, stage=True) for r in rows],
            "absence": None if rows else _absence(
                "none_held", f"the bank holds no {src} row for this storm"),
        }
    adv = newest_official(official_rows)
    same_init = [a for a in advisories(official_rows) if adv and a["init_ts"] == adv["init_ts"]]

    cycles = {}
    for r in cycle_rows:
        cycles.setdefault(r["init_ts"], []).append(r)
    cycle_list = []
    refused = []
    for init in sorted(cycles):
        srcs = []
        for r in sorted(cycles[init], key=lambda r: r["source"]):
            if MEMBER_SOURCE.match(r["source"]):
                refused.append({"source": r["source"], "init_ts": _ts(init),
                                "reason": "ensemble_member"})
                continue
            m = source_meta(r["source"])
            srcs.append({"source": r["source"], "label": m["label"], "role": m["role"],
                         "n_points": r["n_points"], "max_tau_h": r["max_tau"]})
        if srcs:
            cycle_list.append({"init_ts": _ts(init), "sources": srcs})

    return {
        "storm": identity(storm),
        "observed": obs,
        "official": None if adv is None else {
            **_advisory_obj(adv, radii=True),
            "label": source_meta(OFFICIAL)["label"],
            "same_init_advisories": [a["advisory"] for a in same_init],
        },
        "official_absence": None if adv is not None else _absence(
            "no_official_forecast",
            "the bank holds no NHC official forecast for this storm (the advisory GIS has been "
            "banked since 2026-10-09 06Z)"),
        "cycles": cycle_list,
        "cycles_absence": None if cycle_list else _absence(
            "no_model_cycle", "the bank holds no model track for this storm"),
        "refused": refused,
        "radii_convention": RADII_CONVENTION,
        "advisory_rule": ADVISORY_RULE,
        "valid_ts_rule": VALID_TS_RULE,
        **_common(),
    }


# ── /tracks ─────────────────────────────────────────────────────────────────

def build_tracks(*, storm: dict, n: int, rows: list) -> dict:
    """GET /tracks. The newest n model cycles oldest first, one series per source."""
    by_init = {}
    refused = []
    for r in rows:
        if MEMBER_SOURCE.match(r["source"]):
            refused.append({"source": r["source"], "init_ts": _ts(r["init_ts"]),
                            "reason": "ensemble_member",
                            "detail": "an ensemble member's source is never served (rule 3); "
                                      "members live in R2 parquet"})
            continue
        by_init.setdefault(r["init_ts"], []).append(r)
    # A cycle is a model cycle only if it holds a series source; the official
    # rows of the read ride on cycles the read already chose.
    inits = sorted(i for i, rs in by_init.items() if any(r["source"] not in NOT_A_SERIES for r in rs))
    seen_ecmwf = {}
    cycles = []
    present = {}
    for init in inits:
        rs = by_init[init]
        series = []
        for src in sorted({r["source"] for r in rs if r["source"] not in NOT_A_SERIES}):
            m = source_meta(src)
            pts = sorted((r for r in rs if r["source"] == src), key=lambda r: (r["advisory"], r["tau"]))
            notice = _ecmwf_notice(m["ecmwf"], init.astimezone(timezone.utc).year)
            if notice:
                seen_ecmwf.setdefault(m["ecmwf"], set()).add(src)
            series.append({"source": src, "label": m["label"], "status": m["status"],
                           "role": m["role"], "track": m["track"], "notice": notice,
                           "points": [_point(r) for r in pts]})
            present.setdefault(src, []).append(init)
        off = advisories([r for r in rs if r["source"] == OFFICIAL])
        cycles.append({
            "init_ts": _ts(init),
            "series": series,
            "official": [_advisory_obj(a, radii=False) for a in off],
            "official_absence": None if off else _absence(
                "no_official_at_this_init",
                "the bank holds no NHC official forecast with this synoptic time (the advisory "
                "GIS has been banked since 2026-10-09 06Z; the A-deck's OFCL, where held, is a "
                "series)"),
        })
    absent = []
    for src in sorted(present):
        missing = [i for i in inits if i not in present[src]]
        if missing:
            m = source_meta(src)
            absent.append({"source": src, "label": m["label"], "role": m["role"],
                           "adeck": m["adeck"],
                           "present_in": [_ts(i) for i in present[src]],
                           "missing_from": [_ts(i) for i in missing]})
    cur = current_name(storm["names"])
    return {
        "storm_id": storm["storm_id"],
        "name": cur["name"] if cur else None,
        "n": n,
        "n_cycles": len(cycles),
        "order": "oldest first: cycles[-1] is the newest model cycle the bank holds",
        "cycle_rule": ("a model cycle is an init_ts at which the bank holds a point of any "
                       "source but best_track, tcvitals and nhc_official; the official rides "
                       "with the cycle of the same synoptic time"),
        "cycles": cycles,
        "absent": absent,
        "absent_rule": ("a source held in one served cycle and not in another; the late "
                        "A-deck models land hours after the early aids, so the newest cycle "
                        "often holds only the early aids and baselines"),
        "absence": None if cycles else _absence(
            "no_model_cycle", "the bank holds no model track for this storm"),
        "refused": refused,
        "attribution": {k: {"applies_to": sorted(v), "notice": "on each series, as `notice`"}
                        for k, v in sorted(seen_ecmwf.items())},
        "advisory_rule": ADVISORY_RULE,
        "valid_ts_rule": VALID_TS_RULE,
        **_common(),
    }


# ── /odds ───────────────────────────────────────────────────────────────────

PWS_LABEL = "NHC wind speed probabilities (PWS)"
THRESHOLDS_KT = (34, 50, 64)
ODDS_VALUES_RULE = ("value_pct is the probability as stored. Where NHC printed X (less than "
                    "1 %), below_1pct is true and value_pct is null: the bank stores 0 there "
                    "as a placeholder, which is not a value NHC printed, so it is never served "
                    "as 0.")
ODDS_WINDOWS_RULE = ("window_h is the end of NHC's period in hours from the issuance's "
                     "synoptic time (12, 24, 36, 48, 72, 96, 120). cumulative is the "
                     "probability from the start of the first period to window_h; onset is "
                     "the probability that the winds begin in the period ending at window_h. "
                     "At 12 h NHC prints one number, which the bank stores as both.")
ODDS_PLACES_RULE = ("place_id is NHC's own printed name, served as stored; some are grid "
                    "points named by NHC (e.g. 'GFAM 290N 850W'). NHC prints a place only "
                    "when its 5-day cumulative probability is at least 3 % (34 and 50 kt) or "
                    "1 % (64 kt): a place or threshold that is not listed is below that, not 0. "
                    "Places are in the bank's insertion order, which is NHC's printed order.")
PLACE_COORDINATES = {
    "served": False,
    "stop_p": ("no table the API can read holds a place's name or coordinates beyond the "
               "printed place_id. The id is served as stored; no gazetteer is embedded."),
    "pantry_would_bank": ("a tropical_pws_places table keyed by place_id exactly as NHC prints "
                          "it (basin, place_id, display name, latitude, longitude, the source "
                          "of the coordinates and when it was read), joined by place_id."),
}


def build_odds(*, storm: dict, rows: list) -> dict:
    """GET /odds. The newest PWS issuance, per place, threshold and window."""
    cur = current_name(storm["names"])
    body = {"storm_id": storm["storm_id"], "name": cur["name"] if cur else None,
            "source": "nhc_pws", "source_label": PWS_LABEL}
    if not rows:
        body.update({"issuance": None, "places": [], "absence": _absence(
            "no_place_odds", "the bank holds no NHC wind speed probability issuance for "
                             "this storm (NHC issues PWS with regular advisories only)")})
    else:
        r0 = rows[0]
        places, order = {}, []
        for r in rows:
            if r["place_id"] not in places:
                places[r["place_id"]] = {}
                order.append(r["place_id"])
            key = (r["threshold_kt"], r["radius_km"])
            win = places[r["place_id"]].setdefault(key, {}).setdefault(r["window_h"], {})
            win[r["kind"]] = {
                "value_pct": None if r["below_1pct"] else _f(r["value"]),
                "below_1pct": r["below_1pct"], "scored": r["scored"]}
        out = []
        for pid in order:
            ths = []
            for (kt, rad) in sorted(places[pid], key=lambda k: (k[0] is None, k[0], k[1] or 0)):
                ws = places[pid][(kt, rad)]
                ths.append({"threshold_kt": kt, "radius_km": _f(rad), "windows": [
                    {"window_h": w, "cumulative": ws[w].get("cumulative"),
                     "onset": ws[w].get("onset")} for w in sorted(ws)]})
            held = {t["threshold_kt"] for t in ths}
            out.append({
                "place_id": pid, "coordinates": None, "thresholds": ths,
                "thresholds_absent": [
                    {"threshold_kt": kt, "reason": "not_printed",
                     "detail": "NHC printed no row: below its print threshold, not 0"}
                    for kt in THRESHOLDS_KT if kt not in held]})
        body.update({
            "issuance": {"issued_ts": _ts(r0["issued_ts"]), "advisory": r0["advisory"],
                         **correction(rows)},
            "places": out, "absence": None})
    body.update({
        "values_rule": ODDS_VALUES_RULE, "windows_rule": ODDS_WINDOWS_RULE,
        "places_rule": ODDS_PLACES_RULE, "place_coordinates": PLACE_COORDINATES,
        "odds_not_served": {
            "ensemble_frequencies": ("ecmwf_ens and ecmwf_aifs_ens place frequencies: the bank "
                                     "holds none today, and they are counts over ensemble "
                                     "members, unscored, which need their own ruling")},
        **_common()})
    return body
