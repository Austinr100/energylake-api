"""d091741: re-measure d091673's Tropics body sizes after the two not_served pointers.

    python3 -I docs/receipts/tropics-api-index-d091741/rebank_sizes.py

d091673's T10 pins each /tracks body's raw and gzip -6 bytes (docs/receipts/
tropics-api-d091673/sizes.json, re-measured by d091682) to 16 bytes; the file also
holds /storm, /odds and /storms. The pointers add the same bytes to every body;
this re-serves each entry on the d091673 bank, asserts the raw growth is exactly
that (to the cache block's digits), and rewrites raw and gzip6 only
(local_build_ms stays d091682's). Not run by the suite.
"""

import gzip
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import tropics  # noqa: E402
import load_bank_d091673 as lb  # noqa: E402
import load_bank_d091741 as lb3  # noqa: E402

SIZES = ROOT / "docs" / "receipts" / "tropics-api-d091673" / "sizes.json"


def enc(s):
    return json.dumps(s, ensure_ascii=False).encode()


def main_():
    delta = sum(len(enc(tropics.NOT_SERVED[k])) - len(enc(v))
                for k, v in lb3.NOT_SERVED_D091673.items())
    rec = json.loads(SIZES.read_text())
    main._utcnow = lambda: lb.NOW
    client = TestClient(main.app)
    with lb.cluster() as conn:
        main._pool = lb.PgPool(conn)
        def measure(entry, path, label):
            for c in ("_tropics_storms_cache", "_tropics_storm_cache", "_tropics_tracks_cache",
                      "_tropics_odds_cache"):
                getattr(main, c).clear()
            raw = client.get(path).content
            grew = len(raw) - entry["raw"]
            assert abs(grew - delta) <= 16, (label, grew, delta)       # the cache block's digits
            entry["raw"] = len(raw)
            entry["gzip6"] = len(gzip.compress(raw, 6))
            print(label, "raw +", grew, "(pointers +", delta, ")")

        P = "/api/weather/tropics"
        for sid in [k for k in rec if k.startswith(("al", "ep", "cp"))]:
            for key in sorted(rec[sid]):
                path = {"storm": f"{P}/storm?storm_id={sid}", "odds": f"{P}/odds?storm_id={sid}"}.get(
                    key) or f"{P}/tracks?storm_id={sid}&n={int(key[1:])}"
                measure(rec[sid][key], path, f"{sid} {key}")
        measure(rec["storms"], f"{P}/storms", "storms")
    SIZES.write_text(json.dumps(rec, indent=1) + "\n")


if __name__ == "__main__":
    main_()
