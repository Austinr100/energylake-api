"""d091551 — rehearse every new test red, then green.

    python docs/receipts/polled-routes-d091551/rehearse.py HEAD_CHECKOUT > reds.txt

1. This branch's tests against HEAD source (HEAD_CHECKOUT is a checkout of
   d08d602, main before this lane, e.g. `git worktree add DIR d08d602`).
2. Each test against this branch's source with one deliberate break, in a
   throwaway copy: the break is a (file, old, new) string replacement.
3. All of them green on this branch.
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
TESTS = ["tests/test_polled_routes_d091551.py", "tests/test_market_clock.py"]
NEW = "d091551 or R1 or R2 or D1"

BREAKS = [
    ("M1: SET LOCAL outside an explicit transaction", "test_d091551_M1",
     "main.py",
     """            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"SET LOCAL statement_timeout = '{MARKET_CLOCK_STATEMENT_TIMEOUT}'")
                    await cur.execute(MARKET_CLOCK_SQL, params)
                    row = await cur.fetchone()""",
     """            if True:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"SET LOCAL statement_timeout = '{MARKET_CLOCK_STATEMENT_TIMEOUT}'")
                    await cur.execute(MARKET_CLOCK_SQL, params)
                    row = await cur.fetchone()"""),
    ("M2: the cold-memo fixture is not autouse", "test_d091551_M2",
     "tests/test_market_clock.py",
     "@pytest.fixture(autouse=True)\ndef _cold_market_clock_memo():",
     "@pytest.fixture\ndef _cold_market_clock_memo():"),
    ("M3: a failed rebuild is memoised", "test_d091551_M3",
     "main.py",
     """            try:
                payload = await _market_clock_build()""",
     """            try:
                try:
                    payload = await _market_clock_build()
                except Exception:
                    _market_clock_entry = (_market_clock_mono(), {"detail": "db unavailable"})
                    raise"""),
    ("M3: an expired answer is not served while the rebuild runs", "test_d091551_M3",
     "main.py",
     """        if age < MARKET_CLOCK_STALE_MAX:
            _market_clock_spawn_rebuild()
            return hit[1]""",
     ""),
    ("M4: the 503 has no Retry-After", "test_d091551_M4",
     "main.py",
     """        raise HTTPException(status_code=503, detail=str(e),
                            headers={"Retry-After": str(MARKET_CLOCK_RETRY_AFTER)})""",
     """        raise HTTPException(status_code=503, detail=str(e))"""),
    ("R1: the lateral drops the series", "R1",
     "main.py", "WHERE t.dataset = d.dataset AND t.series = d.series",
     "WHERE t.dataset = d.dataset"),
    # The fake pool answers by substring, so a break in the SQL's ORDER BY
    # cannot change R2's body; R2 pins the route's Python over the rows, and
    # the SQL's rows are pinned by plans.md (before/after, same five rows).
    ("R2: the chips are keyed by series, not dataset", "R2",
     "main.py", 'by_dataset = {r["dataset"]: r for r in (driver_rows or [])}',
     'by_dataset = {r["series"]: r for r in (driver_rows or [])}'),
    ("D1: ties go to the latest hour", "D1",
     "main.py", "            if mw > day[\"peak_mw\"]:", "            if mw >= day[\"peak_mw\"]:"),
    ("D1: the memoised body keeps the build's as_of", "D1",
     "main.py", '    out["as_of"] = now.isoformat()        # same key, same place in the body\n',
     ""),
]


def run(cwd, k):
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "-k", k, *TESTS], cwd=cwd, capture_output=True, text=True)
    lines = p.stdout.strip().splitlines()
    failed = [l for l in lines if l.startswith(("FAILED", "ERROR"))]
    return lines[-1] if lines else p.stderr[-300:], failed


def main():
    head = pathlib.Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp) / "head"
        shutil.copytree(head, t, ignore=shutil.ignore_patterns(".git"))
        for f in TESTS:
            shutil.copy(ROOT / f, t / f)
        shutil.copytree(ROOT / "tests/fixtures/polled_routes_d091551",
                        t / "tests/fixtures/polled_routes_d091551", dirs_exist_ok=True)
        shutil.copytree(ROOT / "docs/receipts/polled-routes-d091551",
                        t / "docs/receipts/polled-routes-d091551", dirs_exist_ok=True)
        summary, failed = run(t, NEW)
        print("## 1. this branch's new tests on HEAD source (d08d602)\n")
        print(summary)
        for f in failed:
            print("  ", f[:160])

    print("\n## 2. one deliberate break each, on this branch's source\n")
    for label, k, f, old, new in BREAKS:
        with tempfile.TemporaryDirectory() as tmp:
            t = pathlib.Path(tmp) / "br"
            shutil.copytree(ROOT, t, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            src = (t / f).read_text()
            assert src.count(old) == 1, label
            (t / f).write_text(src.replace(old, new))
            summary, failed = run(t, k)
            print(f"- {label}  [-k {k}]\n    {summary}")
            for x in failed:
                print("     ", x[:160])

    print("\n## 3. green on this branch\n")
    print(run(ROOT, NEW)[0])


if __name__ == "__main__":
    main()
