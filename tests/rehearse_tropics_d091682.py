"""d091682 mutation rehearsal: one break per rule, each must turn the suite red.

    python3 -B tests/rehearse_tropics_d091682.py > docs/receipts/tropics-api-d091682/reds.txt

Each break is a text substitution applied IN PLACE; the guarding tests run
(pytest -B, so no stale bytecode outlives a restore); the file is written back
from its original bytes. The tree is hashed before and after, and the clean
tree (both tropics modules) is run first and last. Not a test module.
"""

import hashlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEST = "tests/test_tropics_rows_in_force_d091682.py"
SUITES = [TEST, "tests/test_tropics_d091673.py"]
RECEIPT = "docs/receipts/tropics-api-d091682/reds.txt"

BREAKS = [
    # rule 1: every read of points and odds is of the rows in force
    ("1 in force", "/tracks reads the bare points table", "tropics.py",
     "               p.vmax_kt, p.mslp_hpa, p.stage, p.revision\n          FROM tropical_track_points_in_force AS p",
     "               p.vmax_kt, p.mslp_hpa, p.stage, p.revision\n          FROM tropical_track_points AS p", "R2 or R7"),
    ("1 in force", "the official forecast reads the bare points table", "tropics.py",
     "      JOIN tropical_track_points_in_force AS p\n        ON p.storm_id = u.storm_id",
     "      JOIN tropical_track_points AS p\n        ON p.storm_id = u.storm_id", "R1 or R7"),
    ("1 in force", "/odds reads the bare odds table", "tropics.py",
     "      JOIN tropical_place_odds_in_force AS o",
     "      JOIN tropical_place_odds AS o", "R3 or R7"),
    # rule 2: corrected is the ledger's word, revision the rows'
    ("2 corrected", "the ledger lookup is dropped (corrected always false)", "tropics.py",
     '    return {"corrected": any(r.get("corrected") is True for r in rows),',
     '    return {"corrected": False,', "R4 or R5"),
    ("2 corrected", "corrected is inferred from changed numbers", "tropics.py",
     '    return {"corrected": any(r.get("corrected") is True for r in rows),',
     '    return {"corrected": any((r.get("revision") or 0) > 0 for r in rows),', "R4 or R5"),
    ("2 corrected", "the official reads the radii zip's word only", "tropics.py",
     "             WHERE v.source = 'nhc' AND v.product IN ('fcst_5day_zip', 'fcst_radii_zip')\n               AND v.storm_id = p.storm_id",
     "             WHERE v.source = 'nhc' AND v.product IN ('fcst_radii_zip')\n               AND v.storm_id = p.storm_id",
     "R4"),
    ("2 corrected", "revision is served as 0 whatever the rows say", "tropics.py",
     '            "revision": max((r.get("revision") or 0 for r in rows), default=0)}',
     '            "revision": 0}', "R1 or R4"),
    ("2 corrected", "an anomaly copy is taken as the copy in force and as a correction",
     "tests/fixtures/tropics_d091682/ddl_294.sql",
     "       (v.meta ? 'correction') AS corrected,\n       (SELECT count(*) FROM tropical_file_vintage a\n         WHERE a.source = v.source AND a.product = v.product AND a.storm_id = v.storm_id\n           AND a.vintage_key = v.vintage_key AND a.status = 'banked'\n           AND a.meta ? 'anomaly_prior_sha') AS anomalies_held\n  FROM tropical_file_vintage v\n WHERE v.status = 'banked' AND NOT (v.meta ? 'anomaly_prior_sha')",
     "       (v.meta ? 'correction' OR v.meta ? 'anomaly_prior_sha') AS corrected,\n       (SELECT count(*) FROM tropical_file_vintage a\n         WHERE a.source = v.source AND a.product = v.product AND a.storm_id = v.storm_id\n           AND a.vintage_key = v.vintage_key AND a.status = 'banked'\n           AND a.meta ? 'anomaly_prior_sha') AS anomalies_held\n  FROM tropical_file_vintage v\n WHERE v.status = 'banked'", "R4"),
    # rule 3: odds as stored, below_1pct as itself
    ("3 odds", "below_1pct is served as 0", "tropics.py",
     '                "value_pct": None if r["below_1pct"] else _f(r["value"]),',
     '                "value_pct": _f(r["value"]),', "R3"),
    # rule 4: no other change to bodies (the vectors)
    ("4 bodies", "a third field rides on the issuance", "tropics.py",
     '            "issuance": {"issued_ts": _ts(r0["issued_ts"]), "advisory": r0["advisory"],\n                         **correction(rows)},',
     '            "issuance": {"issued_ts": _ts(r0["issued_ts"]), "advisory": r0["advisory"],\n                         **correction(rows), "corrected_by": "nhc"},',
     "R6 or V_"),
    ("4 bodies", "the fields move before the advisory's position", "tropics.py",
     '    return {"advisory": a["advisory"], "init_ts": _ts(a["init_ts"]),\n            "position_valid_ts"',
     '    return {"advisory": a["advisory"], "corrected": a["corrected"], "init_ts": _ts(a["init_ts"]),\n            "position_valid_ts"',
     "R6 or V_"),
    # rule 5: a view must not turn an index read into a scan
    ("5 plans", "the points view loses 297's clause", "tests/fixtures/tropics_d091682/ddl_297.sql",
     "                    WHERE q.revision > 0\n                      AND q.storm_id = p.storm_id",
     "                    WHERE q.storm_id = p.storm_id", "R8 or T10"),
    ("5 plans", "the odds view loses 297's clause", "tests/fixtures/tropics_d091682/ddl_297.sql",
     "                    WHERE q.revision > 0\n                      AND q.storm_id = o.storm_id",
     "                    WHERE q.storm_id = o.storm_id", "R8 or T10"),
    # the d091673 repair
    ("R repair", "test_R's base unpinned again (merge-base with main)", "tests/test_tropics_d091673.py",
     'D091673_BASE = "149e314"', 'D091673_BASE = "c40c231"', "no_existing_route"),
]


def tree_hash() -> str:
    files = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard"],
                           capture_output=True, text=True, check=True).stdout.split()
    h = hashlib.sha256()
    for f in sorted(files):
        if f == RECEIPT:                    # this run's own output
            continue
        p = ROOT / f
        if p.is_file():
            h.update(f.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def run(k: str | None) -> tuple[bool, str]:
    cmd = [sys.executable, "-B", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *SUITES]
    if k:
        cmd += ["-k", k]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    last = [l for l in r.stdout.splitlines() if " passed" in l or " failed" in l or " error" in l]
    return r.returncode == 0, (last[-1] if last else r.stdout[-200:]).strip()


def main():
    before = tree_hash()
    ok, line = run(None)
    print(f"clean tree: {'GREEN' if ok else 'RED'} ({line})")
    assert ok
    reds = 0
    for i, (rule, what, rel, old, new, k) in enumerate(BREAKS, 1):
        path = ROOT / rel
        orig = path.read_bytes()
        text = orig.decode()
        assert text.count(old) == 1, (what, text.count(old))
        try:
            path.write_text(text.replace(old, new))
            green, line = run(k)
        finally:
            path.write_bytes(orig)
        reds += not green
        print(f"{i:2d}. rule {rule:13s} {what:55s} -> {'GREEN (missed)' if green else 'RED'}  [{line}]")
    ok, line = run(None)
    after = tree_hash()
    print(f"clean tree again: {'GREEN' if ok else 'RED'} ({line})")
    print(f"{reds} of {len(BREAKS)} breaks red; tree {'restored byte for byte' if before == after else 'CHANGED'} "
          f"(sha256 {before[:16]} -> {after[:16]})")
    sys.exit(0 if reds == len(BREAKS) and before == after and ok else 1)


if __name__ == "__main__":
    main()
