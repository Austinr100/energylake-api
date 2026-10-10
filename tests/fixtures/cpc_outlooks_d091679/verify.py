"""Re-check every banked file against the sha-256 and length Neon computed.

Each file is gzip of the exact `t` text one read-only statement returned (plus
a newline); the manifest's sha-256 and length are Neon's, over that text."""
import gzip, hashlib, json, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
bad = 0
for line in (HERE / "manifest.psv").read_text().splitlines():
    name, n, sha, ln = line.split("|")
    t = gzip.decompress((HERE / (name + ".gz")).read_bytes()).decode().rstrip("\n")
    ok = (hashlib.sha256(t.encode()).hexdigest() == sha and len(t) == int(ln)
          and len(json.loads(t)) == int(n))
    print(("ok  " if ok else "BAD ") + name)
    bad += not ok
print("bad:", bad)
sys.exit(1 if bad else 0)
