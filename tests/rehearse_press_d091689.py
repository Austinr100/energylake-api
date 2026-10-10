"""d091689 mutation rehearsal: one break per rule, each must turn the suite red.

    python3 -B tests/rehearse_press_d091689.py > docs/receipts/press-api-d091689/reds.txt

Each break is a one-line (or one-block) text substitution applied IN PLACE;
the guarding tests run (pytest -B, so no stale bytecode outlives a restore);
the file is written back from its original bytes. The tree is hashed before
and after, and the clean tree is run first and last. Not a test module.
"""

import hashlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEST = "tests/test_press_d091689.py"
RECEIPT = "docs/receipts/press-api-d091689/reds.txt"

BREAKS = [
    # rule 1: the body is served as written
    ("1 as written", "a key is added inside body", "press.py",
     '        "body": r["body"],\n',
     '        "body": {**r["body"], "served_by": "energylake-api"},\n', "A1"),
    ("1 as written", "a key is removed from body", "press.py",
     '        "body": r["body"],\n',
     '        "body": {k: v for k, v in r["body"].items() if k != "disclaimer"},\n', "A1"),
    ("1 as written", "corrected is not revision > 0", "press.py",
     '        "corrected": r["revision"] > 0,\n', '        "corrected": False,\n', "A3"),
    ("1 as written", "sha256 is recomputed from the served body", "press.py",
     '        "sha256": r["sha256"],', '        "sha256": __import__("hashlib").sha256(repr(r["body"]).encode()).hexdigest(),',
     "A1"),
    # rule 2: in force
    ("2 in force", "/front ignores withdrawn_at", "press.py",
     "             WHERE kind = 'daily' AND withdrawn_at IS NULL\n",
     "             WHERE kind = 'daily'\n", "A4 or A5"),
    ("2 in force", "/edition's in force ignores withdrawn_at", "press.py",
     "               AND withdrawn_at IS NULL\n             ORDER BY revision DESC\n             LIMIT 1) AS e ON true",
     "             ORDER BY revision DESC\n             LIMIT 1) AS e ON true", "A4 or A5"),
    ("2 in force", "in force is the lowest revision", "press.py",
     "               AND withdrawn_at IS NULL\n             ORDER BY revision DESC\n             LIMIT 1) AS e ON true",
     "               AND withdrawn_at IS NULL\n             ORDER BY revision ASC\n             LIMIT 1) AS e ON true",
     "A3"),
    ("2 in force", "a withdrawn revision asked for by number is a 404", "press.py",
     "               AND revision = %(revision)s) AS e ON true",
     "               AND revision = %(revision)s AND withdrawn_at IS NULL) AS e ON true", "A4"),
    ("2 in force", "the all-withdrawn 404 does not say so", "press.py",
     '    return (f"every revision of {who} is withdrawn', '    return (f"no edition {who} in the bank', "A5"),
    # rule 3: absence is stated
    ("3 absence", "no daily is a 200 with no sentence", "press.py",
     "    if e is not None:\n        absence = None\n", "    if True:\n        absence = None\n", "A6"),
    ("3 absence", "no daily is a 404", "main.py",
     "    return _pr.build_front(daily=daily[0] if daily else None, headers=headers, now=_utcnow())",
     "    if not daily or daily[0]['revision'] is None:\n        raise HTTPException(status_code=404, detail='no daily')\n"
     "    return _pr.build_front(daily=daily[0] if daily else None, headers=headers, now=_utcnow())",
     "A6"),
    ("3 absence", "the age is measured from the revision in force", "press.py",
     '    first = e["first_submitted_at"] if e else None', '    first = e["submitted_at"] if e else None', "A3"),
    # rule 4: repo rules
    ("4 repo", "n above 60 is trimmed instead of refused", "press.py",
     '    if not 1 <= n <= EDITIONS_N_MAX:\n        raise ParamError("n", f"must be 1-{EDITIONS_N_MAX} editions, got {n}")\n    return n',
     "    return max(1, min(n, EDITIONS_N_MAX))", "A7"),
    ("4 repo", "a slug on a daily is accepted", "press.py",
     "    if kind not in SLUGGED:\n        if given:", "    if kind not in SLUGGED:\n        if False:", "A7"),
    ("4 repo", "/editions reads without the statement timeout", "main.py",
     '    rows = await _dd_timed_read(_pr.HEADERS_SQL, {"kind": kind, "n": n})',
     '    rows = await _dd_read(_pr.HEADERS_SQL, {"kind": kind, "n": n})', "A8 or C_every"),
    ("4 repo", "/front reads one after the other", "main.py",
     "    daily, headers = await asyncio.gather(\n        _dd_timed_read(_pr.FRONT_DAILY_SQL, {}),\n        _dd_timed_read(_pr.FRONT_HEADERS_SQL, {}))",
     "    daily = await _dd_timed_read(_pr.FRONT_DAILY_SQL, {})\n    headers = await _dd_timed_read(_pr.FRONT_HEADERS_SQL, {})",
     "C_front"),
    ("4 repo", "the memo serves stale", "main.py",
     '_press_editions_cache = _DDCache("press/editions", PRESS_MEMO_TTL, max_entries=16,\n',
     '_press_editions_cache = _DDCache("press/editions", PRESS_MEMO_TTL, max_entries=16, allow_stale=True,\n', "C_memo"),
    ("4 repo", "the edition memo key drops revision", "main.py",
     "    return await _press_serve(_press_edition_cache, (k, d, s, rev),",
     "    return await _press_serve(_press_edition_cache, (k, d, s, None),", "C_memo or A3"),
    ("4 repo", "the headers read loses its index", "press.py",
     "    SELECT DISTINCT ON (edition_date, slug) {_HEADER}\n      FROM press_editions\n     WHERE kind = %(kind)s AND",
     "    SELECT DISTINCT ON (edition_date, slug) {_HEADER}\n      FROM press_editions\n     WHERE kind || '' = %(kind)s AND",
     "A9_every"),
    ("4 repo", "a press read names joule_briefs", "main.py",
     "# READ-ONLY, from pantry's press_editions (lane d091688). The Joule brief table",
     "# READ-ONLY, from pantry's press_editions (lane d091688). The joule_briefs table", "A8"),
    # order
    ("order", "/editions is oldest first", "press.py",
     "     ORDER BY edition_date DESC, slug DESC, revision DESC\n     LIMIT %(n)s",
     "     ORDER BY edition_date ASC, slug DESC, revision DESC\n     LIMIT %(n)s", "A7"),
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
    cmd = [sys.executable, "-B", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", TEST]
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
