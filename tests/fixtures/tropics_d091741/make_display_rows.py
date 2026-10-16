"""d091741/d091746: the member manifests the /members tests read, CONSTRUCTED.

The display rows of the advisory objects and the WSP are no longer made here:
d091746 reads them verbatim off the live ledger (bank_display_from_neon.py,
bank/display.jsonl.gz). What is left is the one family nothing has published yet:
no `display:members:*` row is in the ledger (Neon, read 2026-10-15 22:00Z), the
members publish step being d091743's. So the three manifest rows /members serves
or refuses are still built here, from the recon's dry write and the banked
parquets (bank/ledger.jsonl.gz), keyed as the publisher keys a cycle (vintage_key
the cycle, which pantry 314/316's CHECKs admit):

    python3 -I tests/fixtures/tropics_d091741/make_display_rows.py

writes constructed_rows.json. Deterministic: the suite re-runs it and compares
(test_F_the_constructed_manifests_are_the_generators).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import pathlib

FIX = pathlib.Path(__file__).resolve().parent
RECON = FIX / "recon"
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



def constructed_rows():
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
    return members


def main():
    rows = constructed_rows()
    (FIX / "constructed_rows.json").write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n")
    print(len(rows), "constructed manifests")


if __name__ == "__main__":
    main()
