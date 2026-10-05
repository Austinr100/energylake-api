"""d091614 reds: apply each break to a pristine tree, run its tests, restore.

    python docs/receipts/mjo-status-api-d091614/reds.py > docs/receipts/mjo-status-api-d091614/reds.txt
"""
import pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
BREAKS = [
    ("T1", "today is the oldest walk day, not the newest",
     "mjo_status.py", 'newest = walk[-1] if walk and walk[-1]["date"] == frontier.isoformat() else None',
     "newest = walk[0] if walk else None", "test_T1"),
    ("T1", "the frontier is the oldest series' newest day",
     "mjo_status.py", "return max(days) if days else None", "return min(days) if days else None", "test_T1"),
    ("T2", "the walk is served newest first",
     "mjo_status.py", "    return walk, missing, incomplete", "    return walk[::-1], missing, incomplete", "test_T2"),
    ("T2", "each day's source is dropped",
     "mjo_status.py", '        "source": src,', '        "source": None,', "test_T2"),
    ("T2", "the seam names OMI's last day as ROMI's first",
     "mjo_status.py", "            romi_first = as_date(nxt).isoformat()", "            romi_first = omi_last", "test_T2"),
    ("T2r", "rmm is the unrotated pair (PC1, PC2)",
     "mjo_status.py", "    return (pc2, None if pc1 is None else -pc1)", "    return (pc1, pc2)", "test_T2r"),
    ("T2r", "RMM2 = +PC1 (the sign flip dropped)",
     "mjo_status.py", "    return (pc2, None if pc1 is None else -pc1)", "    return (pc2, pc1)", "test_T2r"),
    ("T3", "the threshold drifts from pantry's (0.9)",
     "mjo_status.py", "ACTIVE_THRESHOLD = 1.0", "ACTIVE_THRESHOLD = 0.9", "test_T3"),
    ("T3", "active is recomputed from amplitude > 1.03 instead of served from the bank",
     "mjo_status.py", '        "active": None if act is None else act == 1.0,',
     '        "active": None if vals.get("amplitude") is None else vals["amplitude"] > 1.03,', "test_T3"),
    ("T4", "a missing day is carried forward from the day before",
     "mjo_status.py", "        if d not in vals:\n            missing.append(d.isoformat())\n            continue",
     "        if d not in vals:\n            missing.append(d.isoformat())\n            if walk:\n                walk.append({**walk[-1], 'date': d.isoformat()})\n            continue", "test_T4"),
    ("T4", "a missing day is dropped but not counted",
     "mjo_status.py", "            missing.append(d.isoformat())\n", "", "test_T4"),
    ("P1", "the walk reads the whole dataset (no series predicate)",
     "mjo_status.py", "             WHERE t.dataset = %(dataset)s AND t.series = v.series\n               AND t.ts >= %(lo)s AND t.ts < %(hi)s\n             ORDER BY t.ts) w",
     "             WHERE t.dataset = %(dataset)s\n               AND t.ts >= %(lo)s AND t.ts < %(hi)s\n             ORDER BY t.ts) w", "test_P1"),
    ("P1", "the statement timeout is not set",
     "main.py", "                await cur.execute(\n                    f\"SET LOCAL statement_timeout = '{MJO_STATEMENT_TIMEOUT}'\")\n                await cur.execute(_mjo.NEWEST_SQL",
     "                await cur.execute(_mjo.NEWEST_SQL", "test_P1"),
    ("memo", "the D-09-25-138 stale cap is dropped",
     "main.py", "build_timeout=SOLAR_BUILD_TIMEOUT, max_stale_s=MJO_MAX_STALE_S,\n)\n\n\nasync def _mjo",
     "build_timeout=SOLAR_BUILD_TIMEOUT,\n)\n\n\nasync def _mjo", "test_memo"),
]


def run(k):
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_mjo_status.py", "-q", "-k", k],
                       cwd=ROOT, capture_output=True, text=True)
    return r.returncode, r.stdout.strip().splitlines()[-1]


print("# d091614 reds — each break must turn its tests red; the clean tree must be green\n")
for tag, what, f, old, new, k in BREAKS:
    p = ROOT / f
    src = p.read_text()
    assert src.count(old) == 1, (tag, what)
    p.write_text(src.replace(old, new))
    try:
        code, last = run(k)
    finally:
        p.write_text(src)
    print(f"{tag:<6} {'RED' if code else 'GREEN?!'}   {what}\n       -k '{k}': {last}")
code, last = run("")
print(f"\nclean  {'GREEN' if code == 0 else 'RED?!'}   {last}")
