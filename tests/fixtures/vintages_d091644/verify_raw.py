"""Check each banked issuance text against the md5 Neon computed for it."""
import hashlib, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
bad = 0
for tech in ("solar_pv", "wind"):
    for line in (HERE / f"manifest_{tech}.psv").read_text().splitlines():
        k, init, n, mv, nmv, md5, ln = line.split("|")
        f = HERE / "raw" / tech / f"{int(k):02d}.json"
        if not f.exists():
            print(tech, k, "MISSING"); bad += 1; continue
        t = f.read_text().rstrip("\n")
        ok = hashlib.md5(t.encode()).hexdigest() == md5 and len(t) == int(ln)
        if not ok:
            print(tech, k, init, "MISMATCH", len(t), ln); bad += 1
for i, line in enumerate((HERE / "manifest_dd_pnw_population.psv").read_text().splitlines(), 1):
    src, issued, n, md5, ln = line.split("|")
    f = HERE / "raw" / "dd" / f"{i:02d}.json"
    if not f.exists():
        print("dd", i, "MISSING"); bad += 1; continue
    t = f.read_text().rstrip("\n")
    if hashlib.md5(t.encode()).hexdigest() != md5 or len(t) != int(ln):
        print("dd", i, src, issued, "MISMATCH", len(t), ln); bad += 1
print("bad:", bad)
sys.exit(1 if bad else 0)
