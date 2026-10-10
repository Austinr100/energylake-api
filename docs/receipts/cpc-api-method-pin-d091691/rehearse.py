"""d091691 reds (spec R): one break per rule, each applied to a throwaway copy
of the tree; the tests that guard it must FAIL there, and the unbroken tree
must PASS them. The working tree is never edited, so it is restored byte for
byte by construction; the script also checks its sha-256 before and after.

    python docs/receipts/cpc-api-method-pin-d091691/rehearse.py > docs/receipts/cpc-api-method-pin-d091691/reds.txt

The rules: (1) every reference to the three relations carries the pin, one
break per reference (ten); (2) one constant (the value flipped); (3) the bodies
are main's (a v2 row let through where only a body can tell).
"""
import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
TESTS = ["tests/test_cpc_method_pin_d091691.py", "tests/test_cpc_outlooks_d091679.py"]
FILES = ("cpc_outlooks.py", "main.py", *TESTS, "tests/load_bank_d091691.py")
F = "cpc_outlooks.py"
P1 = "test_P1 or test_P3"
P4 = "test_P4_every_statement"

BREAKS = [
    ("rule 1", "CURVES_SQL: the issuances (iss) unpinned", F,
     "WHERE ci.product = %(product)s AND ci.method_version = {_PIN}",
     "WHERE ci.product = %(product)s", f"{P1} or {P4}"),
    ("rule 1", "CURVES_SQL: the window row (w) unpinned", F,
     "            AND w.method_version = {_PIN}\n", "", f"{P1} or {P4}"),
    ("rule 1", "CURVES_SQL: the verdict-in-force lateral (nv) unpinned", F,
     "WHERE cv.method_hash = w.method_hash AND cv.method_version = {_PIN}",
     "WHERE cv.method_hash = w.method_hash", P4),
    ("rule 1", "CURVES_SQL: the verdict cell (vv) unpinned", F,
     "            AND vv.method_version = {_PIN}\n", "", P4),
    ("rule 1", "CURVES_SQL: the view lateral (dv) unpinned", F,
     "               AND dv.method_version = {_PIN}\n", "", P4),
    ("rule 1", "PLACES_SQL: the hashes unpinned", F,
     "        SELECT DISTINCT hv.method_hash FROM cpc_curve_verdicts hv\n"
     "         WHERE hv.method_version = {_PIN}\n",
     "        SELECT DISTINCT hv.method_hash FROM cpc_curve_verdicts hv\n", P4),
    ("rule 1", "PLACES_SQL: the in-force lateral (lv) unpinned", F,
     "WHERE lv.method_hash = h.method_hash AND lv.method_version = {_PIN}",
     "WHERE lv.method_hash = h.method_hash", P4),
    ("rule 1", "PLACES_SQL: the cells (v) unpinned", F,
     "     WHERE v.method_version = {_PIN}\n", "", P4),
    ("rule 1", "PLACES_NEWEST_SQL: the newest issuance (lc) unpinned", F,
     "WHERE lc.product = p.product AND lc.method_version = {_PIN}",
     "WHERE lc.product = p.product", f"{P1} or {P4}"),
    ("rule 1", "PLACES_NEWEST_SQL: the window rows (c) unpinned", F,
     "AND c.day_index IS NULL AND c.method_version = {_PIN}",
     "AND c.day_index IS NULL", f"{P1} or {P4}"),
    ("rule 2", "the constant flipped to cpc_curves_v2", F,
     'CPC_METHOD_VERSION = "cpc_curves_v1"', 'CPC_METHOD_VERSION = "cpc_curves_v2"',
     f"{P1} or test_P4_one_constant"),
    ("rule 2", "a second literal beside the constant", F,
     "_PIN = f\"'{CPC_METHOD_VERSION}'\"", "_PIN = \"'cpc_curves_v1'\"",
     "test_P4_one_constant"),
    ("rule 3", "the window row pinned by hash instead (lets every v1-hash version through, "
     "v2 rows still out) — P4 must still see it", F,
     "            AND w.method_version = {_PIN}\n",
     "            AND w.method_hash = 'e99c71234013'\n", P4),
]


def shas():
    return {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in FILES}


def run(tree, k):
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS]
    if k:
        args += ["-k", k]
    p = subprocess.run(args, cwd=tree, capture_output=True, text=True, timeout=900)
    return p.returncode, (p.stdout.strip().splitlines() or [""])[-1]


def main():
    ok = True
    before = shas()
    print("# d091691 reds — each break must turn its tests red; the clean tree must be green\n")
    for tag, what, f, old, new, k in BREAKS:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="d091691_red_"))
        try:
            shutil.copytree(ROOT, tmp / "t", ignore=shutil.ignore_patterns(".git", "__pycache__"))
            path = tmp / "t" / f
            src = path.read_text()
            if src.count(old) != 1:
                print(f"{tag:<7} BREAK NOT APPLIED ({what}): needle found {src.count(old)} times")
                ok = False
                continue
            path.write_text(src.replace(old, new))
            rc, tail = run(tmp / "t", k)
            red = rc == 1
            ok &= red
            print(f"{tag:<7} {'RED ' if red else 'NOT RED'}  {what}\n        -k '{k}': {tail}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    rc, tail = run(ROOT, "")
    ok &= rc == 0
    after = shas()
    ok &= before == after
    print(f"\nclean tree: {'GREEN' if rc == 0 else 'RED'}  {tail}")
    print(f"tree restored byte for byte: {before == after} "
          f"({', '.join(f'{f} {m[:12]}' for f, m in after.items())})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
