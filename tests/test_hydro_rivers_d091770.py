"""d091770: the river routes — index, stations, flow, the NWM gate, reaches.

Every cell runs against the BANKED slice (load_bank_d091770: Neon's own row_to_json
text, sha-checked against the sha256 Neon computed over the same expression) in a
throwaway Postgres whose geo tables are the pantry's migrations VERBATIM. The routes'
own statements and bodies run on it unchanged, through main._pool.

Cells
  R1  /index: a river is served when its reach object is banked AND PUBLISHED; the
      Willamette's walk reads no_crosswalk; length_km and the river structure are
      stated absences, never derived; the Columbia serves its PUBLISHED crosswalk and
      names the newer banked-but-unpublished one.
  R2  /stations: the newest record per source_key (the superseded copies are never
      served, and BON's sha differs between them); the Columbia hands the page the
      crosswalk join keyed by record_sha256; the Willamette serves its anchor with
      'nhd_lengthkm' stated and placement 'client_snaps_to_walk'.
  U1  the settled-unit rule: receipted_tags over the NEWEST SET per subject; the
      scope carries the version and the relay basin; a contradicted cwms_catalog row
      convicts nothing outside its own subject; the ported scope_subject equals the
      subject the bank itself banked (PRD's attrs._bank.receipt_scope).
  R3  /flow: a series is served only behind a settled receipt of ITS OWN scope; MCN's
      outflow is grey 'not served'; the mid-Columbia relay rides its own subject.
  R4  /gate: the ported gate_line/line_of reproduce the pantry's dry-run stdout BYTE
      FOR BYTE (all six reach lines, the _no_da twins included, and every decided_by
      line), and the route reads not drawable on today's 0 banked nwm_reach:* receipts.
  R5  tripwires over the module's source: no route reads velocity, no DISTINCT ON, no
      cast in ORDER BY, no OR-ed series.
  X1  every timeseries_values read is an Index Scan on idx_tsv_series_ts with no Seq
      Scan, with buffers stated. NOTE the planner picks a Seq Scan on a 6-row table
      whatever the indexes, so the plan is forced here with enable_seqscan = off and
      the SHAPE is asserted; the authoritative receipts are Neon's own EXPLAIN
      (ANALYZE, BUFFERS) over the live table, in the handback and
      docs/receipts/hydro-rivers-api-d091770/explain_neon.txt.
  W   the window rule: over 14 days is refused, never clipped.

The routes themselves — the statement timeout, the memo, the 400s and their registration
on `main.app` — are tests/test_hydro_rivers_routes_d091770.py (the 10-17 follow-up).
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent),
                str(pathlib.Path(__file__).resolve().parent.parent)]

import load_bank_d091673 as lb673          # noqa: E402  (PgPool)
import load_bank_d091770 as lb              # noqa: E402

import hydro_rivers as hr                   # noqa: E402

COLUMBIA = "nhdplus_hr_columbia_gcl_bon"
WILLAMETTE = "nhdplus_hr_willamette_hcr_columbia"
SNAKE = "nhdplus_hr_snake_lwg_columbia"
SACRAMENTO = "nhdplus_hr_sacramento_shasta_delta"

pytestmark = pytest.mark.skipif(lb.initdb_path() is None,
                                reason="no local initdb to build the throwaway cluster")


@pytest.fixture(scope="module")
def bank():
    with lb.cluster() as conn:
        yield conn


def read(conn, sql, params=None):
    cur = conn.execute(sql, params)
    return cur.fetchall()


def ledger(conn):
    return read(conn, hr.WALKS_SQL), read(conn, hr.RECORDS_SQL)


# ────────────────────────────────────────────────────────────── R1  /index ─────

def test_r1_index_serves_published_walks(bank):
    walks, records = ledger(bank)
    registry = read(bank, hr.REGISTRY_SQL, {"schema": hr.RIVERS_REGISTRY_SCHEMA})[0]
    body = hr.build_index(walks, registry, records)

    assert body["reads_no_r2"] is True
    assert body["omitted"] == [], "every banked walk in the slice is published"
    by_walk = {r["walks"][0]["layer_code"]: r for r in body["rivers"]}
    assert set(by_walk) == {COLUMBIA, SNAKE, SACRAMENTO, WILLAMETTE}

    # The river tag is the ledger's own word, never an invented grouping.
    assert by_walk[COLUMBIA]["river"] == "columbia"
    assert by_walk[COLUMBIA]["river_named_by"].startswith("attrs._bank.river")
    assert by_walk[WILLAMETTE]["river"] == "willamette_hcr_columbia"
    # No record on the Sacramento walk carries attrs._bank.river (its gauges bank a reach).
    assert by_walk[SACRAMENTO]["river"] == "sacramento_shasta_delta"
    assert "the walk's own key" in by_walk[SACRAMENTO]["river_named_by"]

    # The registry is handed over as a pointer; the structure is NOT re-derived here.
    assert body["registry"]["schema"] == "geo.rivers.v1"
    assert body["registry"]["url"].startswith(hr.DISPLAY_BASE)
    for entry in body["rivers"]:
        assert entry["label"] is None and entry["basin"] is None
        assert entry["structure"]["absence"] == "registry_not_read"
        assert entry["length_km"] is None
        assert entry["length_km_absence"]["absence"] == "in_crosswalk_object"


def test_r1_index_crosswalk_published_and_pending(bank):
    walks, records = ledger(bank)
    registry = read(bank, hr.REGISTRY_SQL, {"schema": hr.RIVERS_REGISTRY_SCHEMA})[0]
    by_walk = {r["walks"][0]["layer_code"]: r
               for r in hr.build_index(walks, registry, records)["rivers"]}

    # The Columbia serves the PUBLISHED crosswalk (object 9), not the newer banked
    # object 32, and says a publish is pending rather than dropping the river.
    xw = by_walk[COLUMBIA]["crosswalk"]
    assert xw.get("absence") is None
    assert xw["object_id"] == 9
    assert xw["r2_sha256"] == ("417dfa03c3c05d530b4001620f59b3aaaa51570e351e353ab2b"
                               "79322ba904dae")
    assert xw["sha_equal"] is True
    pending = xw["newer_banked_not_published"]
    assert pending is not None and pending["object_id"] == 32
    assert pending["r2_sha256"] == ("caaf7c89d3e02c7b2bff227758bc72f3aa3a891f732302a05"
                                     "354ef9ba1c2dc9f")

    # D-09-25-247 §3: a crosswalk is optional. The Willamette's walk has none.
    wx = by_walk[WILLAMETTE]["crosswalk"]
    assert wx["absence"] == "no_crosswalk"
    assert "derived from this walk" in wx["why"]
    assert by_walk[WILLAMETTE]["nwm"]["absence"] == "no_crosswalk"
    # ... and it is still served: a reach banked and published is a served river.
    assert by_walk[WILLAMETTE]["walks"][0]["published"]["sha256"] == (
        "217de1179472af72ee556a96f0baed44f57405de9b19c96ca7deeb6572ade17c")


# ─────────────────────────────────────────────────────────── R2  /stations ─────

def test_r2_stations_newest_record_per_source_key(bank):
    walks, records = ledger(bank)
    body = hr.build_stations("columbia", COLUMBIA, walks, records)
    by_key = {s["source_key"]: s for s in body["stations"]}
    assert set(by_key) == {"GCL", "BON", "MCN", "PRD", "WAN"}

    # The superseded copies (57, 59, 62) are never served; the newest are.
    assert by_key["GCL"]["record_id"] == 67
    assert by_key["MCN"]["record_id"] == 69
    assert by_key["BON"]["record_id"] == 72
    # BON's sha CHANGED between the copies, so serving the wrong one would unplace it.
    assert by_key["BON"]["record_sha256"] == ("2ace3788e2a770d59d1f912c909b277128156f5b"
                                              "0b475967eafe2de8f15e1a17")
    assert "LATERAL" in hr.RECORDS_SQL and "DISTINCT ON" not in hr.RECORDS_SQL


def test_r2_stations_placed_is_the_pages_join(bank):
    walks, records = ledger(bank)
    body = hr.build_stations("columbia", COLUMBIA, walks, records)
    bon = next(s for s in body["stations"] if s["source_key"] == "BON")

    assert body["placement"] == "join_crosswalk_places"
    assert bon["placed"] is None, "this route reads no R2, so it never claims a placement"
    join = bon["placed_absence"]["join"]
    assert join["object"] == "nwm_crosswalk_columbia"
    assert join["path"] == "places['BON']"
    assert join["key"] == "record_sha256"
    assert join["record_sha256"] == bon["record_sha256"]
    assert "dist_m <= 1000" in join["placed_iff"]
    # The anchor is served as the anchor it is, on its own scale — never as the walk's km.
    assert bon["anchor"]["km_from_reach_start"] == [727.887, 728.371]
    assert bon["anchor"]["km_is_an_interval"] is True
    assert bon["anchor"]["scale"] == "nhd_lengthkm"


def test_r2_stations_without_a_crosswalk_serve_the_anchor(bank):
    walks, records = ledger(bank)
    body = hr.build_stations("willamette_hcr_columbia", WILLAMETTE, walks, records)
    assert body["crosswalk"]["absence"] == "no_crosswalk"
    assert body["placement"] == "client_snaps_to_walk"
    hcr = next(s for s in body["stations"] if s["source_key"] == "HCR")
    assert hcr["placement"] == "client_snaps_to_walk"
    assert hcr["unplaced"]["absence"] == "no_crosswalk"
    assert hcr["unplaced"]["scale"] == "nhd_lengthkm"
    # Its own published lat/lon and anchor are served (D-09-25-237 §2).
    assert (hcr["lat"], hcr["lon"]) == (43.7098056, -122.4218056)
    assert hcr["anchor"]["reach"] == WILLAMETTE
    assert hcr["anchor"]["reach_object_sha256"] == (
        "217de1179472af72ee556a96f0baed44f57405de9b19c96ca7deeb6572ade17c")


# ──────────────────────────────────────────── U1  the settled-unit rule ───────

def test_u1_receipted_tags_over_the_newest_set(bank):
    sets = hr.receipt_sets(read(bank, hr.RECEIPTS_SQL))

    # cwms_forebay_hourly's newest set is the 20:05Z one (receipt 69) ALONE: the
    # 15:03Z rows, inconclusive ones included, are not the set in force.
    fb = sets["cwms_forebay_hourly"]
    assert fb["receipt_ids"] == [69]
    assert fb["tags"] == {"ft"}
    # A contradicted row under cwms_catalog:* convicts its own subject, nothing else.
    assert sets["cwms_catalog:GCL.Elev-Forebay.Inst.1Hour.0.CBT-REV"]["tags"] == set()
    assert sets["cwms_outflow_hourly"]["tags"] == {"cfs"}
    assert sets["usgs_iv_discharge"]["tags"] == {"ft^3/s"}
    # The gate has run in plan mode only: not one nwm_reach:* receipt is banked.
    assert [s for s in sets if s.startswith("nwm_reach:")] == []


def test_u1_the_ported_scope_equals_the_banked_scope(bank):
    _walks, records = ledger(bank)
    prd = next(r for r in records if r["source_key"] == "PRD")
    stated = prd["attrs"]["_bank"]["receipt_scope"]["cwms_outflow_hourly"]
    # The port reproduces the subject the ingester itself banked, byte for byte.
    assert hr.scope_subject("PRD", "cwms_outflow_hourly") == stated
    assert stated == ("cwms_outflow_hourly:Flow-Out.Ave.1Hour.1Hour.CBT-REV"
                      "@mid_columbia")
    subject, how, disagreement = hr.subject_of(prd, "cwms_outflow_hourly")
    assert (subject, disagreement) == (stated, None)
    assert how.startswith("attrs._bank.receipt_scope")
    # A relay is its own scope: the PUDs' numbers never ride the TDA/GCL receipt.
    assert hr.scope_subject("GCL", "cwms_outflow_hourly") == "cwms_outflow_hourly"
    # A version named in VERSIONS is its own scope too (LGS/LMN/IHR serve .Best).
    assert hr.scope_subject("LGS", "cwms_outflow_hourly") == (
        "cwms_outflow_hourly:Flow-Out.Ave.1Hour.1Hour.Best")


# ─────────────────────────────────────────────────────────────── R3  /flow ────

def flow_body(conn, river, walk):
    walks, records = ledger(conn)
    recs = hr.shape_ledger(walks, records)["records"].get(walk, [])
    stations = [{"source_key": r["source_key"], "serves": hr.station_series(r)[0]}
                for r in recs]
    pairs = [(ds, key) for ds, key, _ in hr.flow_pairs(stations)]
    values = []
    if pairs:
        limit = max(hr.window_limit(ds, lb.WIN_FROM, lb.WIN_TO) for ds, _ in pairs)
        params = [x for pair in pairs for x in pair] + [lb.WIN_FROM, lb.WIN_TO, limit]
        values = read(conn, hr.series_window_sql(len(pairs)), params)
    receipts = read(conn, hr.RECEIPTS_SQL)
    gate = hr.build_gate(river, walk, walks, records, receipts, set())
    return hr.build_flow(river, walk, walks, records, receipts, values, gate,
                         start=lb.WIN_FROM, end=lb.WIN_TO)


def test_r3_flow_serves_only_behind_a_settled_receipt(bank):
    body = flow_body(bank, "columbia", COLUMBIA)
    series = {(s["source_key"], x["dataset"]): x
              for s in body["stations"] for x in s["series"]}

    # MCN's release is not served by the source at all (d091715) -> grey, with the word.
    mcn = series[("MCN", "cwms_outflow_hourly")]
    assert mcn["state"] == "grey"
    assert mcn["why"].startswith("not served: MCN.Flow-Out.Ave.1Hour.1Hour.CBT-REV")

    # GCL's outflow rides cwms_outflow_hourly, settled on 'cfs' -> served, with values.
    gcl = series[("GCL", "cwms_outflow_hourly")]
    assert gcl["state"] == "served"
    assert gcl["subject"] == "cwms_outflow_hourly"
    assert (gcl["unit_tag"], gcl["unit_settled"]) == ("cfs", "cfs")
    assert gcl["receipt_ids"] == [31, 33]
    assert list(gcl["values"].values()) == [51200.0, 55800.0, 46800.0]

    # The mid-Columbia relay rides ITS OWN subject, not the Columbia's.
    prd = series[("PRD", "cwms_outflow_hourly")]
    assert prd["state"] == "served"
    assert prd["subject"].endswith("@mid_columbia")
    assert prd["receipt_ids"] == [137, 138, 139, 140]
    assert prd["scope"]["relay_basin"] == "mid_columbia"

    # PRD's forebay is not served by the SOURCE, so it never reaches the receipt gate.
    assert series[("PRD", "cwms_forebay_hourly")]["why"].startswith("not served: PRD.")

    # WAN's forebay IS served by the source, and its relay subject's newest set settles
    # NO tag (receipt 141, inconclusive). That is the receipt gate itself: the reason
    # names the receipt, not "no rows", which is a different absence entirely.
    wan_fb = series[("WAN", "cwms_forebay_hourly")]
    assert wan_fb["state"] == "grey"
    assert wan_fb["subject"] == ("cwms_forebay_hourly:Elev-Forebay.Inst.1Hour.0."
                                 "CBT-REV@mid_columbia")
    assert wan_fb["why"].startswith("no settled unit receipt for subject"), wan_fb["why"]
    assert "settles no tag" in wan_fb["why"] and "inconclusive" in wan_fb["why"]
    assert wan_fb["receipt_ids"] == [141]
    # Its outflow, on the same relay scope, IS settled — so the gate is per SUBJECT,
    # not per station.
    assert series[("WAN", "cwms_outflow_hourly")]["subject"].endswith("@mid_columbia")

    # NWM: never velocity, and never drawn while the gate is shut.
    assert body["nwm"]["drawn"] is False
    assert body["nwm"]["velocity"]["absence"] == "never_served"


def test_r3_flow_window_is_refused_not_clipped():
    with pytest.raises(ValueError, match="at most 14 are served"):
        hr.parse_window("2026-09-01", "2026-10-17", now=lb.WIN_TO)
    start, end = hr.parse_window("2026-10-10", "2026-10-17", now=lb.WIN_TO)
    assert (end - start).days == 7
    # Each read's LIMIT is the window's own row count, per the dataset's cadence.
    assert hr.window_limit("cwms_outflow_hourly", start, end) == 7 * 24 + 2
    assert hr.window_limit("usgs_iv_discharge", start, end) == 7 * 96 + 2


# ─────────────────────────────────────────────────────────────── R4  /gate ────

def test_r4_flow_verdict_needs_twenty_days_and_the_tolerance():
    """The gate's own arithmetic, ported from scripts/nwm_reach_gate.flow_verdict:
    settled only at n >= MIN_DAYS AND within TOL of 1.0. A banked receipt carries its
    verdict, so without this cell nothing in the suite reads MIN_DAYS at all."""
    assert (hr.MIN_DAYS, hr.TOL) == (20, 0.20)
    # n below the floor is inconclusive however good the statistic.
    assert hr.flow_verdict(1.0, hr.MIN_DAYS - 1) == "inconclusive"
    assert hr.flow_verdict(1.0, 0) == "inconclusive"
    assert hr.flow_verdict(None, 400) == "inconclusive"
    # At the floor, the tolerance decides, and the edge is inclusive.
    assert hr.flow_verdict(1.0, hr.MIN_DAYS) == "settled"
    assert hr.flow_verdict(1.0 + hr.TOL, hr.MIN_DAYS) == "settled"
    assert hr.flow_verdict(1.0 - hr.TOL, hr.MIN_DAYS) == "settled"
    assert hr.flow_verdict(1.0 + hr.TOL + 1e-9, hr.MIN_DAYS) == "contradicted"
    assert hr.flow_verdict(35.3147, 40) == "contradicted"
    # And the gate only calls a reach drawable on SETTLED items, never on a verdict
    # the floor would have refused.
    n_short = {"verdict": hr.flow_verdict(1.0, hr.MIN_DAYS - 1), "statistic": 1.0,
               "n_obs": hr.MIN_DAYS - 1, "_detail": {}}
    line, ok = hr.gate_line("reach", [("Q4", "GCL", n_short, None)])
    assert ok is False
    assert line == "reach: not drawable — decided by Q4 GCL: inconclusive (1.0, n 19)"




def test_r4_the_port_reproduces_the_pantrys_stdout_byte_for_byte():
    fx = lb.gate_fixture()
    stdout = lb.gate_stdout().splitlines()
    by_subject = {r["subject"]: r for r in fx["receipts"]}
    reach_lines, checked = [], 0

    for layer, xw in hr.LAYER_REACH.items():
        reach = fx["reaches"][xw]
        for suffix, key, ok_key in ((("", "line", "drawable")),
                                    (":no_da", "no_da_line", "no_da_drawable")):
            items = []
            for printed in reach["decided_by" if not suffix else "no_da_decided_by"]:
                q, rest = printed.split(" ", 1)
                name = rest.split(":", 1)[0]
                r = by_subject.get(f"nwm_reach:{layer}:{name}{suffix}")
                why = None if r else hr.GATE_ARBITER[q][1]
                items.append((q, name, r, why))
                # line_of reproduces each decided_by line, hour pairs included.
                assert hr.line_of(*items[-1]) == printed
                checked += 1
            line, ok = hr.gate_line(xw, items)
            if suffix:
                line = "_no_da " + line
            assert line == reach[key], f"{xw}{suffix}"
            assert ok is reach[ok_key]
            reach_lines.append(line)

    # The pantry's own six lines, in its order, byte for byte.
    assert [stdout[0], stdout[1].lstrip(), stdout[2], stdout[3].lstrip(),
            stdout[4], stdout[5].lstrip()] == [
        reach_lines[0], reach_lines[1], reach_lines[2],
        reach_lines[3], reach_lines[4], reach_lines[5]]
    assert checked == 40, checked          # 20 items x the da / _no_da twin
    # MCN is an item with no receipt, and the port spells its reason the pantry's way.
    assert ("Q4 MCN: no banked release (cwms_outflow_hourly holds no series for it)"
            in fx["reaches"][hr.LAYER_REACH["nwm_crosswalk_columbia"]]["decided_by"])


def test_r4_gate_is_not_drawable_on_todays_bank(bank):
    walks, records = ledger(bank)
    receipts = read(bank, hr.RECEIPTS_SQL)
    # The arbiter set, read the way main._hydro_arbiter_present reads it: one LATERAL
    # per series with LIMIT 1. Only the locations the bank holds a release for answer.
    dams = [r for r in hr.shape_ledger(walks, records)["records"][COLUMBIA]
            if r["kind"] == "cwms_project"]
    pairs = [("cwms_outflow_hourly", r["source_key"]) for r in dams]
    present = {(r["dataset"], r["series"]) for r in read(bank, f"""
        SELECT d.dataset, d.series
        FROM (VALUES {hr.values_sql(len(pairs))}) AS d(dataset, series)
        CROSS JOIN LATERAL (
            SELECT 1 FROM timeseries_values t
            WHERE t.dataset = d.dataset AND t.series = d.series
            ORDER BY t.ts DESC LIMIT 1
        ) AS v""", [x for pair in pairs for x in pair])}
    assert present == {("cwms_outflow_hourly", "GCL"), ("cwms_outflow_hourly", "PRD")}
    gate = hr.build_gate("columbia", COLUMBIA, walks, records, receipts, present)

    assert gate["banked"] == 0
    assert gate["drawable"] is False and gate["no_da_drawable"] is False
    assert gate["question"] == "Q4"
    assert gate["line"].startswith(f"{COLUMBIA}: not drawable — decided by Q4 GCL:")
    assert hr.NO_GATE_RECEIPT_WHY in gate["line"]
    assert gate["no_da_line"].startswith("_no_da ")
    # A line recomputed from the bank carries NO hour-pairs clause: none is banked.
    assert "hour pairs" not in gate["line"]
    assert gate["hourly_pairs_absence"]["absence"] == "not_banked"
    # The gate orders its items by the ledger's own anchor km, upstream first:
    # GCL 0.0, WAN 291.0, PRD 320.7, MCN 489.9, BON 727.9.
    assert [i["name"] for i in gate["items"]] == ["GCL", "WAN", "PRD", "MCN", "BON"]
    # The three with no banked release carry the pantry's own wording for it.
    assert [i["name"] for i in gate["items"] if i["why"] == hr.GATE_ARBITER["Q4"][1]] == [
        "WAN", "MCN", "BON"]
    # GCL and PRD have a release but no gate receipt, so they carry the other reason.
    assert [i["name"] for i in gate["items"]
            if i["why"] == hr.NO_GATE_RECEIPT_WHY] == ["GCL", "PRD"]
    # No forecast is served while the line is shut (D-09-25-232 §3).
    assert gate["forecast"] is None
    assert gate["forecast_absence"]["absence"] == "gate_not_drawable"


def test_r4_gate_and_reaches_read_no_crosswalk_without_one(bank):
    walks, records = ledger(bank)
    receipts = read(bank, hr.RECEIPTS_SQL)
    gate = hr.build_gate("willamette_hcr_columbia", WILLAMETTE, walks, records,
                         receipts, set())
    assert gate["absence"] == "no_crosswalk" and gate["drawable"] is False
    reaches = hr.build_reaches("willamette_hcr_columbia", WILLAMETTE, walks, records,
                               gate, [], start=lb.WIN_FROM, end=lb.WIN_TO)
    assert reaches["absence"] == "no_crosswalk" and reaches["reaches"] == []

    shut = hr.build_gate("columbia", COLUMBIA, walks, records, receipts, set())
    out = hr.build_reaches("columbia", COLUMBIA, walks, records, shut, [],
                           start=lb.WIN_FROM, end=lb.WIN_TO)
    assert out["absence"] == "gate_not_drawable"
    assert out["why"] == shut["line"]
    assert out["velocity"]["absence"] == "never_served"


# ──────────────────────────────────────────────────────── R5  tripwires ───────

def all_sql() -> dict:
    """Every statement this lane can emit: the module's SQL constants, the SQL it
    builds, and main.py's one inline read."""
    main_src = (pathlib.Path(hr.__file__).parent / "main.py").read_text()
    rivers = main_src[main_src.index("THE RIVERS (lane d091770)"):]
    inline = rivers[rivers.index("    sql = f\"\"\""):rivers.index("\"\"\"\n    await cur.execute(sql")]
    return {
        "WALKS_SQL": hr.WALKS_SQL, "REGISTRY_SQL": hr.REGISTRY_SQL,
        "RECORDS_SQL": hr.RECORDS_SQL, "RECEIPTS_SQL": hr.RECEIPTS_SQL,
        # FORECAST_CYCLE_SQL was removed in the 10-17 follow-up: nothing executed it,
        # and no statement here can name a <feature_id>/<product>/<cycle> series.
        "series_window_sql(ASC)": hr.series_window_sql(2),
        "series_window_sql(DESC)": hr.series_window_sql(2, order="DESC"),
        "main._hydro_arbiter_present": inline,
    }


def test_r5_no_route_reads_velocity():
    src = pathlib.Path(hr.__file__).read_text()
    main_src = (pathlib.Path(hr.__file__).parent / "main.py").read_text()
    rivers = main_src[main_src.index("THE RIVERS (lane d091770)"):]

    # NWM velocity is never read (D-09-25-237). The ban is on the SOURCES of it — a
    # series, dataset, column or route that would carry it. The bodies DO carry a
    # `velocity` key: it holds the absence that says the term is never served.
    for name, text in (("hydro_rivers.py", src), ("main.py's rivers section", rivers)):
        low = text.lower()
        for token in ("river_velocity", "/velocity", "streamflow_velocity",
                      "velocity_hourly", "velocity_bank", "nwm_reach_velocity"):
            assert token not in low, f"{name} names {token}"
    # No line that mentions velocity may also READ anything: the word appears only in
    # a body key, a body-shape listing, or a statement that the term is never served.
    reads = ("select ", "execute", "fetchall", "fetchone", "t.dataset", "t.series",
             "timeseries_values", "dataset =", "series =")
    for text in (src, rivers):
        for line in text.splitlines():
            low = line.lower()
            if "velocity" in low:
                assert not any(r in low for r in reads), line
    # And no SQL this lane emits mentions it at all.
    for name, sql in all_sql().items():
        assert "velocity" not in sql.lower(), name


def test_r5_no_banned_sql_in_any_statement():
    """D-08-02-L / d091551: never DISTINCT ON, never a cast in ORDER BY, never OR-ed
    series. Checked over the STATEMENTS, not the prose that explains the rule."""
    for name, sql in all_sql().items():
        low = " ".join(sql.lower().split())
        assert "distinct on" not in low, f"{name} uses DISTINCT ON"
        assert "series = any(" not in low, f"{name} OR-es its series"
        assert " or t.series" not in low and " or series" not in low, name
        for order in [low[i:i + 60] for i in range(len(low)) if low.startswith("order by", i)]:
            assert "::" not in order.split("limit")[0], f"{name} casts in ORDER BY: {order}"
    # The only DISTINCT in the lane is DISTINCT (a key list), never DISTINCT ON.
    assert "SELECT DISTINCT layer_code" in hr.WALKS_SQL
    assert "SELECT DISTINCT kind, source_code, source_key" in hr.RECORDS_SQL


def test_r5_every_series_read_is_one_dataset_series_pair():
    sql = hr.series_window_sql(3)
    assert sql.count("%s") == 3 * 2 + 3           # 3 pairs + from, to, limit
    assert "t.dataset = d.dataset AND t.series = d.series" in sql
    assert "ORDER BY t.ts ASC" in sql and "LIMIT %s" in sql
    assert "CROSS JOIN LATERAL" in sql
    with pytest.raises(ValueError):
        hr.series_window_sql(1, order="t.ts::date")


# ─────────────────────────────────────────────────────────────── X1  plans ────

@pytest.mark.parametrize("name,sql,params", [
    ("flow", hr.series_window_sql(2),
     ["cwms_outflow_hourly", "GCL", "cwms_outflow_hourly", "PRD",
      lb.WIN_FROM, lb.WIN_TO, 26]),
])
def test_x1_series_reads_are_index_scans(bank, name, sql, params):
    # A 6-row table is cheapest to seq-scan whatever the indexes, so the plan is forced
    # and its SHAPE asserted. Neon's own EXPLAIN over the live table is the receipt.
    bank.execute("SET enable_seqscan = off")
    try:
        plan = "\n".join(
            r["QUERY PLAN"] for r in
            read(bank, f"EXPLAIN (ANALYZE, BUFFERS) {sql}", params))
    finally:
        bank.execute("SET enable_seqscan = on")
    assert "idx_tsv_series_ts" in plan, plan
    assert "Index Scan" in plan or "Index Only Scan" in plan, plan
    assert "Seq Scan on timeseries_values" not in plan, plan
    assert "Buffers:" in plan, plan
    # Deliberately writes nothing: a test that leaves a file behind breaks the
    # rehearsal's tree hash, which is how R's "left no trace" claim is checked.
    # The receipts live in docs/receipts/hydro-rivers-api-d091770/.


def test_x1_the_ledger_reads_take_one_row_per_key(bank):
    plan = "\n".join(r["QUERY PLAN"] for r in
                     read(bank, f"EXPLAIN (ANALYZE, BUFFERS) {hr.RECORDS_SQL}"))
    assert "Buffers:" in plan
    # One LATERAL per key, each keeping a single row: never a group-wide scan.
    assert plan.count("Limit") >= 1
    assert "DISTINCT ON" not in hr.RECORDS_SQL and "DISTINCT ON" not in hr.WALKS_SQL
