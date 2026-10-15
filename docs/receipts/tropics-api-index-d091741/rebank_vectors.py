"""d091741: re-bank d091673's 11 Tropics vectors with the two not_served pointers.

    python3 -I docs/receipts/tropics-api-index-d091741/rebank_vectors.py

The brief points `not_served.ensemble_members` at the new /members route; this lane
also points `not_served.cone_and_warnings` at /products (the same move: the statement
"not served" would otherwise outlive the index of the same objects). Every Tropics
body carries not_served, so every banked vector changes by exactly those two strings
and nothing else. This script substitutes them (each must occur once per file) and
writes the file back; d091673's V tests then prove the served bodies equal the files,
and test_tropics_index_d091741 V1 proves each file minus the two pointers is the body
before this lane, byte for byte (the vectors' shas at 265f9fb). Not run by the suite.
"""

import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import tropics  # noqa: E402  (the new wording)
import load_bank_d091741 as lb  # noqa: E402  (the old wording, pinned)

BODIES = ROOT / "tests" / "fixtures" / "tropics_d091673" / "bodies"


def enc(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def main():
    for f in sorted(BODIES.glob("*.json")):
        raw = f.read_text()
        before = hashlib.sha256(raw.encode()).hexdigest()
        for key in ("ensemble_members", "cone_and_warnings"):
            old, new = enc(lb.NOT_SERVED_D091673[key]), enc(tropics.NOT_SERVED[key])
            assert raw.count(old) == 1, (f.name, key)
            raw = raw.replace(old, new)
        f.write_text(raw)
        print(f.name, before[:12], "->", hashlib.sha256(raw.encode()).hexdigest()[:12])


if __name__ == "__main__":
    main()
