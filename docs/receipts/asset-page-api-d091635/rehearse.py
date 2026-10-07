"""Mutation rehearsal, d091635 API: each one-line break must turn the suite red.

    python docs/receipts/asset-page-api-d091635/rehearse.py
"""
import hashlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
BREAKS = [
    ("asset_page.py", 'hour = tuple(c for c in HOUR_DRIVERS[tech] if ("implied_gen_site_latest", c) in have)',
     'hour = tuple(HOUR_DRIVERS[tech])', "drivers assumed present instead of read off the catalog"),
    ("asset_page.py", 'SCORE_WHO = "registry"', 'SCORE_WHO = "calibrated"',
     "the plant set beside the hub's CALIBRATED score (not like for like)"),
    ("asset_page.py", '    kept = [r for r in hour_rows if r["init_ts"] == init]', '    kept = list(hour_rows)',
     "an older cycle's hours mixed into the newest"),
    ("asset_page.py", '    return {"pat": f"%{esc}%", "prefix": f"{esc}%", "code": code}',
     '    return {"pat": f"%{q}%", "prefix": f"{q}%", "code": code}', "LIKE wildcards in the query read as patterns"),
    ("asset_page.py", '    if tech == "wind" and hub in wo.NO_SCORE_AREAS:', '    if False:',
     "ZP26 wind quoted a score row it does not have"),
    ("main.py", """                    f"SET LOCAL statement_timeout = '{SOLAR_STATEMENT_TIMEOUT}'")
                await cur.execute(_ap.COLUMNS_SQL, _ap.columns_params(tech))""",
     """                    "SELECT 1")
                await cur.execute(_ap.COLUMNS_SQL, _ap.columns_params(tech))""", "the asset read loses its statement timeout"),
]
TEST = "tests/test_asset_page_d091635.py"


def sha(p):
    return hashlib.sha256((ROOT / p).read_bytes()).hexdigest()


before = {p: sha(p) for p in {b[0] for b in BREAKS}}
ok = True
for path, old, new, what in BREAKS:
    f = ROOT / path
    src = f.read_bytes()
    assert src.count(old.encode()) == 1, (path, old[:60])
    f.write_bytes(src.replace(old.encode(), new.encode()))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", TEST],
                           cwd=ROOT, capture_output=True, text=True)
    finally:
        f.write_bytes(src)
    tail = [l for l in r.stdout.splitlines() if l.startswith("FAILED") or " passed" in l or " failed" in l]
    red = r.returncode != 0
    ok &= red
    print(f"{'RED  ' if red else 'GREEN'} {what}\n      {tail[0] if tail else ''}")
after = {p: sha(p) for p in before}
print("tree restored byte for byte:", after == before, after)
sys.exit(0 if ok and after == before else 1)
