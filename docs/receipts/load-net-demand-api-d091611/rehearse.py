"""d091611 reds: each break below is applied to a throwaway copy of the tree,
the tests that guard it must FAIL there, and the unbroken tree must PASS them.

    python docs/receipts/load-net-demand-api-d091611/rehearse.py > docs/receipts/load-net-demand-api-d091611/reds.txt
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]

BREAKS = [
    ("L1", "each day takes the longest horizon (7DA) instead of the freshest product",
     "load_outlook.py",
     "        for p in PRODUCTS:\n            n = sum(",
     "        for p in reversed(PRODUCTS):\n            n = sum(",
     "test_L1"),
    ("L1", "the hours lose their issue time",
     "load_outlook.py",
     '                "issued_at": _iso(issued),\n',
     '                "issued_at": None,\n',
     "test_L1_production"),
    ("L1", "D+3..D+6 stop saying their age (D-09-25-139 clause 3)",
     "load_outlook.py",
     '            "issued_days_ago": (today - idate).days if idate else None,\n',
     '            "issued_days_ago": None,\n',
     "test_L1_D3"),
    ("L2", "a BA whose EIA-930 D fails the rule is scored anyway",
     "load_outlook.py",
     '    if not basis["usable"]:\n        return {k: not_scored(',
     '    if False:\n        return {k: not_scored(',
     "test_L2"),
    ("L2", "the footprint (scale) test is dropped from the rule",
     "load_outlook.py",
     '                  "pass": scale is not None and R["scale_min"] <= scale <= R["scale_max"]},\n',
     '                  "pass": True},\n',
     "test_L2 or test_L3_areas"),
    ("L3", "every area with an EIA code gets a DF line, the system included",
     "load_outlook.py",
     '    if a["kind"] != "weim_ba":\n        return None\n    return a["eia"]',
     '    return a["eia"]',
     "test_L3"),
    ("L4", "days past D+7 are drawn",
     "load_outlook.py",
     "    for k in range(MAX_DAYS_AHEAD + 1):\n",
     "    for k in range(MAX_DAYS_AHEAD + 3):\n",
     "test_L4"),
    ("N1", "a registry figure stands in for a missing calibrated one",
     "load_outlook.py",
     '    if cid is None:\n        return None, "registry_only"\n',
     '    if cid is None:\n        return float(row.get("registry_mw") or 0), None\n',
     "test_N1"),
    ("N1", "the fitted-lead gate is ignored (D-09-25-127)",
     "load_outlook.py",
     "    if lo <= lead <= hi:\n        return float(cal), None\n",
     "    if True:\n        return float(cal), None\n",
     "test_N1"),
    ("N2", "the backtest pairs D with the 06Z issuance of D-1",
     "load_outlook.py",
     "datetime.min.time(), UTC) + timedelta(hours=12)\n",
     "datetime.min.time(), UTC) + timedelta(hours=6)\n",
     "test_N2"),
    ("N3", "CAISO's DA net demand is drawn with a hub missing",
     "load_outlook.py",
     "        if set(got) >= need:\n",
     "        if got:\n",
     "test_N3"),
    ("N4", "13 days score",
     "load_outlook.py",
     "MIN_SCORED_DAYS = 14\n",
     "MIN_SCORED_DAYS = 13\n",
     "test_N4"),
    ("P1", "the shifted hour is inlined again (the 6.8 s plan)",
     "load_outlook.py",
     "    ds AS MATERIALIZED (\n",
     "    ds AS (\n",
     "test_P1"),
    ("P1", "a timeseries read that names no series",
     "load_outlook.py",
     "     WHERE t.dataset = %(dataset)s AND t.series = %(series)s\n",
     "     WHERE t.dataset = %(dataset)s\n",
     "test_P1"),
    ("D-09-25-138", "the 15-minute stale cap is ignored",
     "main.py",
     "            if self.allow_stale and (self.max_stale_s is None\n"
     "                                     or age < self.ttl + self.max_stale_s):\n",
     "            if self.allow_stale:\n",
     "test_stale_cap"),
    ("D-09-25-75", "the statement timeout dropped from /outlook",
     "main.py",
     "                await cur.execute(\n                    f\"SET LOCAL statement_timeout = '{LOAD_STATEMENT_TIMEOUT}'\")\n"
     "                await cur.execute(_lo.FCST_SQL, {\"area\": area, **rng})\n",
     "                await cur.execute(_lo.FCST_SQL, {\"area\": area, **rng})\n",
     "test_reads_run_after_the_statement_timeout"),
]

TESTS = ["tests/test_load_outlook.py"]


def run(tree, k):
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS]
    if k:
        args += ["-k", k]
    p = subprocess.run(args, cwd=tree, capture_output=True, text=True, timeout=300)
    return p.returncode, (p.stdout.strip().splitlines() or [""])[-1]


def main():
    ok = True
    print("# d091611 reds — each break must turn its tests red; the clean tree must be green\n")
    for tag, what, f, old, new, k in BREAKS:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="d091611_red_"))
        try:
            shutil.copytree(ROOT, tmp / "t", ignore=shutil.ignore_patterns(".git", "__pycache__"))
            path = tmp / "t" / f
            src = path.read_text()
            if src.count(old) != 1:
                print(f"{tag}  BREAK NOT APPLIED ({what}): needle found {src.count(old)} times")
                ok = False
                continue
            path.write_text(src.replace(old, new))
            rc, tail = run(tmp / "t", k)
            red = rc != 0
            ok &= red
            print(f"{tag:<11} {'RED ' if red else 'NOT RED'}  {what}\n            -k '{k}': {tail}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    rc, tail = run(ROOT, "")
    ok &= rc == 0
    print(f"\nclean tree: {'GREEN' if rc == 0 else 'RED'}  {tail}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
