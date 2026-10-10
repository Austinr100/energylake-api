"""d091691 reds (spec R): one break per rule, each applied to a throwaway copy
of the tree; tests/test_cpc_method_pin_d091691.py must go red there, and the
unbroken tree must be green. The working tree is never edited, so it is
restored byte for byte by construction; the script also checks the sha-256
of every tracked and untracked file before and after.

For each break it reports which test groups went red, so a pin that only the
source test (P4) can see is named as such: it is implied by another pin.

    python docs/receipts/cpc-api-method-pin-d091691/rehearse.py > docs/receipts/cpc-api-method-pin-d091691/reds.txt
"""
import collections
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
TESTS = "tests/test_cpc_method_pin_d091691.py"
CO = "cpc_outlooks.py"
V1 = "{_PIN}"

BREAKS = [
    # rule 1: the issuances are the pinned version's
    ("1", "CURVES_SQL issuances read any version (iss pin removed)", CO,
     " AND c.method_version = {_PIN}\n         ORDER BY issued_date DESC\n         LIMIT %(n)s",
     "\n         ORDER BY issued_date DESC\n         LIMIT %(n)s", "P1 P3"),
    # rule 2: one window row per issuance and place (the view's own pin keeps
    # v2's days out, so this break serves v2's window row, not doubled days)
    ("2", "CURVES_SQL window row of any version (w pin removed)", CO,
     "            AND w.method_version = {_PIN}\n", "", "P1"),
    # rule 3: the verdict in force is the pinned version's
    ("3", "CURVES_SQL nv lateral unpinned", CO,
     " AND cv.method_version = {_PIN}", "", "P4"),
    ("3", "CURVES_SQL vv cell unpinned", CO,
     "            AND vv.method_version = {_PIN}\n", "", "P4"),
    # rule 4: the curve is the pinned version's view rows
    ("4", "CURVES_SQL view lateral unpinned (the tie to w kept)", CO,
     "               AND dv.method_version = {_PIN}\n", "", "P4"),
    ("4", "CURVES_SQL view lateral's tie to the window row dropped (the pin kept)", CO,
     "               AND dv.method_version = w.method_version\n", "", "P4"),
    # rule 5: /places' cells are the pinned version's
    ("5", "PLACES_SQL hashes unpinned", CO,
     "\n         WHERE hv.method_version = {_PIN}", "", "P4"),
    ("5", "PLACES_SQL inforce lateral unpinned", CO,
     " AND v.method_version = {_PIN}\n                 ORDER BY", "\n                 ORDER BY", "P4"),
    ("5", "PLACES_SQL cells unpinned", CO,
     "     WHERE v.method_version = {_PIN}\n", "", "P4"),
    ("5", "PLACES_SQL all three pins removed", CO,
     None, None, "P1 P2"),
    # rule 6: /places' newest issuance is the pinned version's
    ("6", "PLACES_NEWEST_SQL newest issuance of any version", CO,
     " AND c.method_version = {_PIN}\n                 ORDER BY issued_date DESC",
     "\n                 ORDER BY issued_date DESC", "P1 P3"),
    ("6", "PLACES_NEWEST_SQL window rows of any version", CO,
     "       AND c.day_index IS NULL AND c.method_version = {_PIN}\n",
     "       AND c.day_index IS NULL\n", "P1"),
    # rule 7: one constant, the architect's
    ("7", "the constant set to cpc_curves_v2", CO,
     'CPC_METHOD_VERSION = "cpc_curves_v1"', 'CPC_METHOD_VERSION = "cpc_curves_v2"', "P1 P4"),
    ("7", "the pin removed everywhere (main's cpc_outlooks.py)", CO,
     "MAIN", None, "P1 P2 P3 P4"),
    # rule 8: the plans are of the statements served
    ("8", "a pinned read edited without its plan re-taken", CO,
     "            AND w.weighting = %(weighting)s AND w.day_index IS NULL\n",
     "            AND w.weighting = %(weighting)s AND w.day_index IS NULL"
     " AND w.day_index IS NULL\n", "P5"),
]


def tree_sha() -> str:
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.split()
    h = hashlib.sha256()
    for f in sorted(files):
        if f.endswith("cpc-api-method-pin-d091691/reds.txt"):
            continue      # this run's own output
        p = ROOT / f
        if p.is_file():
            h.update(f.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def run(tree: pathlib.Path) -> tuple[str, collections.Counter]:
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "-rf", TESTS], cwd=tree, capture_output=True, text=True)
    summary = out.stdout.strip().splitlines()[-1]
    red = collections.Counter(m.group(1) for m in re.finditer(
        r"^FAILED \S+::test_(P\d)_", out.stdout, re.M))
    return summary, red


def apply(tree: pathlib.Path, fname, old, new):
    p = tree / fname
    s = p.read_text()
    if old == "MAIN":
        s = subprocess.run(["git", "show", f"HEAD:{fname}"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout
    elif old is None:      # PLACES_SQL: all three pins
        for o, n in (("\n         WHERE hv.method_version = {_PIN}", ""),
                     (" AND v.method_version = {_PIN}\n                 ORDER BY",
                      "\n                 ORDER BY"),
                     ("     WHERE v.method_version = {_PIN}\n", "")):
            assert s.count(o) == 1, o
            s = s.replace(o, n)
    else:
        assert s.count(old) == 1, (fname, old)
        s = s.replace(old, new)
    p.write_text(s)


def main():
    before = tree_sha()
    print("# d091691 reds — each break must turn tests red; the clean tree must be green")
    print("# red groups: P1 bodies/rows, P2 duplication, P3 newest, P4 source, P5 plans, P6 flip\n")
    with tempfile.TemporaryDirectory() as d:
        clean = pathlib.Path(d) / "clean"
        shutil.copytree(ROOT, clean, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        summary, red = run(clean)
        print(f"clean   {'GREEN' if not red else 'RED!!'}  {summary}\n")
        ok = not red
        for rule, what, fname, old, new, expect in BREAKS:
            tree = pathlib.Path(d) / "broken"
            if tree.exists():
                shutil.rmtree(tree)
            shutil.copytree(clean, tree)
            apply(tree, fname, old, new)
            summary, red = run(tree)
            groups = " ".join(f"{g}:{n}" for g, n in sorted(red.items()))
            hit = bool(red) and all(g in red for g in expect.split())
            ok &= hit
            print(f"rule {rule}  {'RED  ' if hit else 'MISS!'} {what}")
            print(f"        expected {expect}; red {groups or '(none)'}; {summary}")
    after = tree_sha()
    print(f"\nworking tree sha-256 before {before}")
    print(f"working tree sha-256 after  {after}  {'(identical)' if before == after else 'CHANGED!'}")
    sys.exit(0 if ok and before == after else 1)


if __name__ == "__main__":
    main()
