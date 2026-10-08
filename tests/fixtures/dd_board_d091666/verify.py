"""Re-check every banked file against the md5 and length Neon computed."""
import hashlib, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
bad = 0
for line in (HERE / "manifest.psv").read_text().splitlines():
    name, _n, md5, ln = line.split("|")
    t = (HERE / name).read_text().rstrip("\n")
    if hashlib.md5(t.encode()).hexdigest() != md5 or len(t) != int(ln):
        print(name, "MISMATCH"); bad += 1
print("bad:", bad)
sys.exit(1 if bad else 0)
