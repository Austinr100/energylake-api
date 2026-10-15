"""d091741: the display-bucket ledger rows the index routes read, CONSTRUCTED.

d091740 (the pantry's publish step) had not landed and its spec was not
readable from this lane, so no `display:*` row exists anywhere (Neon, read
2026-10-15 13:56Z: none). These rows are what recon d091737 §7 says the
publish step will write, built from the recon's own parsed fixtures (pantry
@ 81ad901, copied under recon/, each sha pinned in manifest.json):

  * one ledger row per published object: source 'display' (migration 314; tropics_index.
    DISPLAY_SOURCE), product 'display:<name>', r2_key = the display key,
    sha256/byte_size = the parsed object's (the recon's canonical parse:
    recorded_k1's parsed_sha256 and parsed_bytes; the parsed text JSON's own
    bytes), meta = the K1 summary plus the NHC source's key and sha;
  * the source sha of every row is checked against the banked ledger slice
    (bank/ledger.jsonl.gz), so a row can only name a file the bank holds.

    python3 -I tests/fixtures/tropics_d091741/make_display_rows.py

writes display_rows.json (the base layer: I1, T1, M1) and constructed_rows.json
(the second layer: I2's correction and three member manifests). Deterministic:
the suite re-runs it and compares (test_F_the_display_rows_are_the_generators).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import pathlib

FIX = pathlib.Path(__file__).resolve().parent
RECON = FIX / "recon"
K1 = json.loads((RECON / "recorded_k1.json").read_text())
LEDGER = [json.loads(l) for l in gzip.decompress((FIX / "bank" / "ledger.jsonl.gz").read_bytes())
          .decode().split("\n")]
CONSTRUCTED_BY = "d091741 (constructed from recon d091737's parsed fixtures; not d091740's rows)"


def banked(source, product, storm_id, vkey):
    rows = [r for r in LEDGER if (r["source"], r["product"], r["storm_id"], r["vintage_key"])
            == (source, product, storm_id, vkey) and r["status"] == "banked"
            and "anomaly_prior_sha" not in r["meta"]]
    assert rows, (source, product, storm_id, vkey)
    return max(rows, key=lambda r: (r["fetch_ts"], r["vintage_id"]))


def row(vid, dataset, product, storm_id, vkey, r2_key, sha, nbytes, issued_ts, meta,
        fetch_ts="2026-10-15T13:30:00+00:00"):
    return {"vintage_id": vid, "dataset": dataset, "source": "display", "product": product,
            "storm_id": storm_id, "vintage_key": vkey, "status": "banked", "fetch_ts": fetch_ts,
            "issued_ts": issued_ts, "r2_key": r2_key, "byte_size": nbytes, "sha256": sha,
            "source_url": None, "source_last_modified": None, "source_etag": None,
            "meta": meta, "closed_ts": None}


def geo_meta(src, layer):
    return {"source_r2_key": src["r2_key"], "source_sha256": src["sha256"],
            "geometry_types": layer["geometry_types"], "vertices": layer["vertices"],
            "n_features": layer["n_features"], "lon_range": layer["lon_range"],
            "constructed_by": CONSTRUCTED_BY}


def advisory_object(vid, fixture, name, nhc_product, storm_id, adv):
    k = K1[fixture]
    src = banked("nhc", nhc_product, storm_id, adv)
    assert k["input_sha256"] == src["sha256"], fixture          # the recon parsed the banked bytes
    layer = k["layers"]["_"]
    return row(vid, "nhc_advisory_vintage", f"display:{name}", storm_id, adv,
               f"tropics/{storm_id}/adv{adv}/{name}.geojson", layer["parsed_sha256"],
               layer["parsed_bytes"], src["issued_ts"], geo_meta(src, layer))


def text_object(vid, storm_id, adv, product):
    f = RECON / f"{storm_id}_adv{adv}_{product}.json"
    raw = f.read_bytes()
    p = json.loads(raw)
    src = banked("nhc", product, storm_id, adv)
    meta = {"source_r2_key": src["r2_key"], "source_sha256": src["sha256"],
            "issued_utc": p["issued_utc"], "transmitted_utc": p["transmitted_utc"],
            "forecaster": p["forecaster"], "wmo_heading": p["wmo_heading"],
            "sections": [{"title": s["title"], "kind": s["kind"]} for s in p["sections"]],
            "constructed_by": CONSTRUCTED_BY}
    return row(vid, "nhc_advisory_vintage", f"display:{product}", storm_id, adv,
               f"tropics/{storm_id}/adv{adv}/{product}.json", hashlib.sha256(raw).hexdigest(),
               len(raw), src["issued_ts"], meta)


def wsp_objects(vid, cycle, nhc_product, kind):
    k = K1[f"wsp_{cycle}_{nhc_product}.zip"]
    src = banked("nhc", nhc_product, "", cycle)
    assert k["input_sha256"] == src["sha256"], (cycle, nhc_product)
    out = []
    for kt in ("34", "50", "64"):
        layer = k["layers"][kt]
        out.append(row(vid, "nhc_wsp_vintage", f"display:wsp{kt}_{kind}", "", cycle,
                       f"tropics/wsp/{cycle}/wsp{kt}_{kind}.geojson", layer["parsed_sha256"],
                       layer["parsed_bytes"], src["issued_ts"], geo_meta(src, layer)))
        vid += 1
    return out


def base_rows():
    rows = [
        advisory_object(910001, "ep202026_adv032_cone_kmz.kmz", "cone", "cone_kmz", "ep202026", "032"),
        advisory_object(910002, "ep182026_adv058_cone_kmz.kmz", "cone", "cone_kmz", "ep182026", "058"),
        advisory_object(910003, "ep182026_adv057A_ww_kmz.kmz", "ww", "ww_kmz", "ep182026", "057A"),
        advisory_object(910004, "ep202026_adv028A_ww_kmz.kmz", "ww", "ww_kmz", "ep202026", "028A"),
        advisory_object(910005, "al092026_adv014A_ww_kmz.kmz", "ww", "ww_kmz", "al092026", "014A"),
        advisory_object(910006, "al092026_adv014A_surge_ww_kml.kml", "surge_ww", "surge_ww_kml",
                        "al092026", "014A"),
        text_object(910011, "ep182026", "058", "tcd"),
        text_object(910012, "ep182026", "058", "tcp"),
        text_object(910013, "ep202026", "032", "tcd"),
        text_object(910014, "ep202026", "032", "tcp"),
    ]
    rows += wsp_objects(910021, "2026101112", "wsp_5km_zip", "bands")
    rows += wsp_objects(910024, "2026101112", "wsp_halfdeg_zip", "grid")
    rows += wsp_objects(910027, "2026101506", "wsp_5km_zip", "bands")
    # 2026101506's half-degree grid is banked and NOT published here: the recon
    # parsed only 2026101112's (recorded_k1), so it stays not_published.
    return rows


def constructed_rows(base):
    r0 = [r for r in base if (r["product"], r["storm_id"], r["vintage_key"]) ==
          ("display:cone", "ep202026", "032")][0]
    note = b"d091741 constructed revision 1 of tropics/ep202026/adv032/cone.geojson"
    # Keyed as the publisher keys a revision (pantry 314: vintage_key '<ADV>r<n>', one row
    # per (product, storm_id, vintage_key)); the API's revision read is the lane after this.
    r1 = dict(r0, vintage_id=910101, vintage_key="032r1", fetch_ts="2026-10-15T13:40:00+00:00",
              r2_key="tropics/ep202026/adv032/cone.r1.geojson",
              sha256=hashlib.sha256(note).hexdigest(), byte_size=r0["byte_size"] + 12,
              meta=dict(r0["meta"], vertices=r0["meta"]["vertices"] + 4,
                        correction={"revision": 1, "prior_sha256": [r0["sha256"]],
                                    "signal": "constructed for I2 (a corrected advisory)"}))
    dry = RECON / "members_dry_ep202026_2026101112_ecmwf_ens_manifest.json"
    raw = dry.read_bytes()
    m = json.loads(raw)
    taus = sorted(int(t) for t in m["members_by_tau"])
    simon = banked("parsed", "tracks:ecmwf_ens", "ep202026", "2026101112")
    rachel = banked("parsed", "tracks:ecmwf_ens", "ep182026", "2026101112")
    isaias = banked("parsed", "tracks:ecmwf_aifs_ens", "al092026", "2026101006")
    assert isaias["meta"]["n_members"] == 7
    members = [
        row(910201, "tc_model_tracks", "display:members:ecmwf_ens", "ep202026", "2026101112",
            "tropics/ep202026/2026101112/ecmwf_ens/manifest.json", hashlib.sha256(raw).hexdigest(),
            len(raw), simon["issued_ts"],
            {"source_r2_key": simon["r2_key"], "source_sha256": simon["sha256"],
             "n_members": m["n_members"], "min_members": 5, "taus": taus,
             "synthetic_input": True,
             "constructed_by": CONSTRUCTED_BY + "; the manifest is the recon's dry write from a "
                                                "SYNTHETIC parquet"}),
        row(910202, "tc_model_tracks", "display:members:ecmwf_ens", "ep182026", "2026101112",
            "tropics/ep182026/2026101112/ecmwf_ens/manifest.json",
            hashlib.sha256(b"d091741 constructed Rachel 2026101112 ecmwf_ens manifest").hexdigest(),
            900, rachel["issued_ts"],
            {"source_r2_key": rachel["r2_key"], "source_sha256": rachel["sha256"],
             "n_members": rachel["meta"]["n_members"], "min_members": 5, "taus": [0, 12],
             "constructed_by": CONSTRUCTED_BY}),
        # Isaias 2026101006 AIFS-ENS holds 7 members (the bank's meta); its manifest
        # states a minimum of 10. The API must take the manifest's word: 7 is above
        # the recon's provisional 5, so a floor the API made up would serve it.
        row(910203, "tc_model_tracks", "display:members:ecmwf_aifs_ens", "al092026", "2026101006",
            "tropics/al092026/2026101006/ecmwf_aifs_ens/manifest.json",
            hashlib.sha256(b"d091741 constructed Isaias 2026101006 ecmwf_aifs_ens manifest").hexdigest(),
            1400, isaias["issued_ts"],
            {"source_r2_key": isaias["r2_key"], "source_sha256": isaias["sha256"],
             "n_members": isaias["meta"]["n_members"], "min_members": 10, "taus": [0, 6, 12, 18, 24],
             "constructed_by": CONSTRUCTED_BY}),
    ]
    return [r1] + members


def main():
    base = base_rows()
    (FIX / "display_rows.json").write_text(json.dumps(base, indent=1, sort_keys=True) + "\n")
    (FIX / "constructed_rows.json").write_text(
        json.dumps(constructed_rows(base), indent=1, sort_keys=True) + "\n")
    print(len(base), "display rows;", len(constructed_rows(base)), "constructed")


if __name__ == "__main__":
    main()
